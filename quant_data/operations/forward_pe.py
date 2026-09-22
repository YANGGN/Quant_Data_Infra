"""Manual, retained-data-only forward P/E builder. No provider calls or source writes."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import zlib

from ..company.forward_pe import VERSION, LABEL, SPLIT_ONLY, PERIOD_MATCHING_POLICY, RATIO_POLICY, ESTIMATE_PERIOD_POLICY, digest, make_windows, daily_series
from ..market.provider_close_series import ProviderClosePriceRepository
from ..stores import acquire_write_session, quiet_immutable_read_connection, StoreMap

PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MAX_SYMBOLS = 2500
MAX_SOURCE_ROWS = 30000
MAX_SECONDS = 3600


def fixed_stores():
    return StoreMap.from_mapping({r: PROJECT_ROOT / "data" / (r + ".sqlite")
                                  for r in ("market", "company", "macro", "news")})


def bounded(connection, query, params=(), limit=MAX_SOURCE_ROWS):
    rows = [dict(r) for r in connection.execute(query + " LIMIT ?", (*params, limit + 1))]
    if len(rows) > limit:
        raise ValueError("Source row limit exceeded; nothing was silently truncated")
    return rows


@lru_cache(maxsize=2)
def session_calendar(end):
    # The selected equity universe consists of US listings. Nasdaq and NYSE
    # share these regular cash-equity session closes; unknown venues are flagged.
    import exchange_calendars
    calendar = exchange_calendars.get_calendar("XNYS", start="1980-01-01", end=end)
    return [(str(day.date()), row["close"].isoformat())
            for day, row in calendar.schedule.iterrows()]


def subjects(stores, symbols, cutoff, *, strict=True):
    with acquire_write_session(stores, ("company",), timeout_seconds=5):
        with quiet_immutable_read_connection(stores, "company") as c:
            rows = bounded(c, """SELECT DISTINCT symbol,issuer_id,cik,instrument_id
              FROM company_fmp_analyst_captures
              WHERE endpoint='analyst-estimates' AND request_period='quarter' AND captured_at<=?""",
                           (cutoff,), limit=MAX_SYMBOLS * 2)
    if symbols:
        requested = set(symbols)
        rows = [r for r in rows if r["symbol"] in requested]
        missing = requested - {r["symbol"] for r in rows}
        if missing and strict:
            raise ValueError("No retained quarterly estimates for: " + ", ".join(sorted(missing)))
    counts = Counter(r["symbol"] for r in rows)
    if strict and any(n != 1 for n in counts.values()):
        raise ValueError("Ambiguous ticker identity; select an unambiguous ticker")
    rows = [r for r in rows if counts[r["symbol"]] == 1]
    if len(rows) > MAX_SYMBOLS:
        raise ValueError("Ticker limit exceeded")
    return sorted(rows, key=lambda r: r["symbol"])



def attach_sharadar_links(stores, selected, cutoff):
    """Reuse the established same-membership cross-provider association."""
    from ..market.collection_bindings import load_bindings, pin_binding
    bindings = load_bindings(PROJECT_ROOT / "config" / "collection_bindings.json")
    with acquire_write_session(stores, ("market",), timeout_seconds=5):
        fmp = pin_binding(stores, bindings["fmp_statements"], cutoff=cutoff)
        sf = pin_binding(stores, bindings["sharadar_fundamentals"], cutoff=cutoff)
    if fmp.membership_snapshot_id != sf.membership_snapshot_id:
        return selected
    by_source = {s.source_symbol: s for s in sf.eligible}
    by_identity = {}
    for item in fmp.eligible:
        other = by_source.get(item.source_symbol)
        if other is not None:
            by_identity.setdefault((item.instrument_id, item.cik), []).append(other)
    result = []
    for item in selected:
        matches = by_identity.get((item["instrument_id"], item["cik"]), [])
        identities = {(m.provider_symbol, m.provider_subject) for m in matches}
        if len(identities) == 1:
            ticker, subject = identities.pop()
            item = dict(item, sharadar_ticker=ticker, sharadar_subject=subject,
                        membership_snapshot_id=fmp.membership_snapshot_id,
                        fmp_mapping_id=fmp.mapping_id, sharadar_mapping_id=sf.mapping_id,
                        identity_link_status="same_collection_member_partial_identity")
        result.append(item)
    # One bounded metadata scan, rather than scanning the source-key table for
    # every ticker. Values and lineage are still selected inside each read cohort.
    with acquire_write_session(stores, ("company",), timeout_seconds=5):
        with quiet_immutable_read_connection(stores, "company") as c:
            lookup = bounded(c, "SELECT observation_id,source_ticker FROM company_sharadar_sf1_observations WHERE dimension='ARQ'",
                             limit=1000000)
    ids = {}
    for row in lookup:
        ids.setdefault(row["source_ticker"], []).append(row["observation_id"])
    for item in result:
        item["sharadar_observation_ids"] = ids.get(item.get("sharadar_ticker", item["symbol"]), [])
    return result

def read_inputs(stores, subject, cutoff, start, end, *, changed_since=None, history_start="1980-01-01"):
    """One coordinated immutable read cohort per instrument, bounded to 30 seconds."""
    began = time.monotonic()
    with acquire_write_session(stores, ("company", "market"), timeout_seconds=5):
        with ExitStack() as stack:
            c = stack.enter_context(quiet_immutable_read_connection(stores, "company"))
            m = stack.enter_context(quiet_immutable_read_connection(stores, "market"))
            for connection in (c, m):
                connection.set_progress_handler(lambda: int(time.monotonic() - began > 30), 10000)
            def analyst(endpoint):
                return bounded(c, """SELECT * FROM company_fmp_analyst_observation_versions
                  INDEXED BY company_fmp_analyst_latest
                  WHERE issuer_id=? AND endpoint=? AND instrument_id=? AND captured_at<=?
                  AND (request_period='quarter' OR endpoint='earnings')""",
                  (subject["issuer_id"], endpoint, subject["instrument_id"], cutoff))
            estimates = analyst("analyst-estimates")
            earnings = analyst("earnings")
            statements = bounded(c, """SELECT * FROM company_fmp_research_rows
              WHERE cik=? AND endpoint='income-statement' AND request_period='quarter'
              AND captured_at<=?""", (subject["cik"], cutoff))
            # Retained ARQ supplies older actual period ends; its filing date is
            # evidence metadata, never the earnings announcement date.
            observation_ids = subject.get("sharadar_observation_ids")
            if observation_ids is not None and len(observation_ids) > 5000:
                raise ValueError("Sharadar observation-key bound exceeded")
            key_filter = ("" if observation_ids is None else
                          " AND o.observation_id IN (" + ",".join("?" for _ in observation_ids) + ")"
                          if observation_ids else " AND 0")
            sharadar_query = """SELECT * FROM (
              SELECT o.observation_id,v.version_id,v.reportperiod,v.source_datekey,
                     v.available_at,v.values_json,
                     ROW_NUMBER() OVER(PARTITION BY o.observation_id ORDER BY v.version_sequence DESC) selected
              FROM company_sharadar_sf1_observations o
              JOIN company_sharadar_sf1_versions v ON v.observation_id=o.observation_id
              WHERE o.source_ticker=? AND o.dimension='ARQ' AND v.available_at<=? AND v.ingested_at<=?
                AND EXISTS(SELECT 1 FROM company_sharadar_identity_assertions a
                  WHERE a.capture_id=v.capture_id AND a.source_ticker=o.source_ticker
                    AND ((a.instrument_id=? AND a.cik=?) OR
                         (a.provider_subject=? AND a.membership_snapshot_id=?))
                    AND a.available_at<=?))
              WHERE selected=1"""
            sharadar_query = sharadar_query.replace("o.dimension='ARQ'", "o.dimension='ARQ'" + key_filter)
            sharadar = bounded(c, sharadar_query,
              (subject.get("sharadar_ticker", subject["symbol"]), *(observation_ids or ()), cutoff, cutoff,
               subject["instrument_id"], subject["cik"], subject.get("sharadar_subject"),
               subject.get("membership_snapshot_id"), cutoff), limit=5000)
            fmp_ends = {r["period_end"] for r in statements}
            for row in sharadar:
                original_end = row["reportperiod"]
                near = [p for p in fmp_ends if abs(
                    (date.fromisoformat(p)-date.fromisoformat(original_end)).days) <= 7]
                statements.append(dict(
                    natural_identity="sharadar:" + row["observation_id"],
                    research_row_id=row["version_id"], period_end=near[0] if len(near)==1 else original_end,
                    source_period_end=original_end, captured_at=row["available_at"],
                    fiscal_year=None, fiscal_period="quarter", source="sharadar_ARQ",
                    payload_json=row["values_json"], source_datekey=row["source_datekey"]))
            transcripts = bounded(c, """SELECT capture_id,event_json,fiscal_year,fiscal_quarter,captured_at
              FROM company_equibles_transcripts WHERE symbol=? AND instrument_id=? AND captured_at<=?""",
              (subject["symbol"], subject["instrument_id"], cutoff))
            instrument = m.execute("""SELECT * FROM stage10_instruments
              WHERE instrument_id=? AND captured_at<=?""", (subject["instrument_id"], cutoff)).fetchone()
            price_filter = "trade_date BETWEEN ? AND ?"
            price_args = (start, end)
            if changed_since is not None:
                # Capture timestamps can predate commit. Scan bounded published versions
                # across saved history; the refresh diffs identities before any write.
                price_filter = "trade_date BETWEEN ? AND ?"
                price_args = (history_start, end)
            prices = bounded(m, """SELECT * FROM (SELECT v.*,
              ROW_NUMBER() OVER(PARTITION BY trade_date ORDER BY correction_sequence DESC) selected
              FROM stage10_daily_price_versions v WHERE instrument_id=?
              AND """ + price_filter + """ AND captured_at<=? AND available_at<=?)
              WHERE selected=1 ORDER BY trade_date""",
              (subject["instrument_id"], *price_args, cutoff, cutoff))
            prices = ProviderClosePriceRepository._with_capture_binding(m, prices)
            basis = (SPLIT_ONLY if prices and all(
                r["_provider_close_binding_established"] for r in prices) else "not_established")
            freshness = {endpoint: c.execute(
                "SELECT MAX(captured_at) FROM company_fmp_analyst_captures WHERE issuer_id=? "
                "AND instrument_id=? AND endpoint=? AND captured_at<=? "
                "AND (request_period='quarter' OR endpoint='earnings')",
                (subject["issuer_id"], subject["instrument_id"], endpoint, cutoff)).fetchone()[0]
                for endpoint in ("analyst-estimates", "earnings")}
            stamps = {}
            for role in ("company", "market"):
                st = stores.path(role).stat()
                stamps[role] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    venue = instrument["exchange_code"] if instrument else None
    flags = ["local_source_snapshot", "mixed_capture_share_basis_unverified"]
    if subject.get("identity_link_status"):
        flags.append(subject["identity_link_status"])
    if venue is None:
        flags.append("us_listing_calendar_assumed")
    elif venue.upper() not in {"NASDAQ", "NYSE", "AMEX", "NYSEAMERICAN", "NYSEARCA", "BATS", "NASDAQGS",
                               "NASDAQGM", "NASDAQCM", "XNYS", "XNAS"}:
        flags.append("unsupported_exchange")
        basis = "not_established"
    if prices:
        start = max(start, prices[0]["trade_date"])
    return dict(subject, cutoff=cutoff, start=start, end=end, estimates=estimates,
                earnings=earnings, statements=statements, transcripts=transcripts, prices=prices,
                price_basis=basis, price_currency=None, share_basis=None, split_basis_date=None,
                flags=flags, source_stamps=stamps, freshness=freshness, calendar="XNYS")


SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE source_inputs (
 instrument_id TEXT PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, content_sha256 TEXT NOT NULL,
 inputs_zlib BLOB NOT NULL);
CREATE TABLE windows (
 window_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL REFERENCES source_inputs(instrument_id),
 symbol TEXT NOT NULL, effective_date TEXT NOT NULL, announcement TEXT NOT NULL,
 reported_period_end TEXT, forward_eps REAL, status TEXT NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE daily (
 instrument_id TEXT NOT NULL REFERENCES source_inputs(instrument_id),
 symbol TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL, forward_eps REAL,
 forward_pe_proxy REAL, status TEXT NOT NULL,
 window_id TEXT REFERENCES windows(window_id), price_version_id TEXT,
 PRIMARY KEY (instrument_id,trade_date));
CREATE INDEX daily_symbol_date ON daily(symbol,trade_date);
CREATE VIEW daily_forward_pe AS
 SELECT d.*,w.announcement,w.reported_period_end,
 json_extract(w.details_json,'$.components') AS components_json,
 json_extract(w.details_json,'$.flags') AS flags_json,
 'Daily forward P/E - reconstructed estimates' AS series_label,
 0 AS is_point_in_time
 FROM daily d LEFT JOIN windows w ON w.window_id=d.window_id;
"""


def materialize(output, selected, read, sessions, *, cutoff, start, end, allow_unverified_basis, progress=None):
    """Write a new private derivative. Internal path injection is for offline fixtures."""
    if output.exists():
        raise FileExistsError("Existing research builds are immutable")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_suffix(".partial.sqlite")
    with staging.open("xb"):
        pass
    statuses = Counter()
    summary = []
    source_hashes = {}
    started = time.monotonic()
    try:
        with sqlite3.connect(staging) as out:
            out.execute("PRAGMA foreign_keys=ON")
            out.executescript(SCHEMA)
            for index, subject in enumerate(selected, 1):
                if time.monotonic() - started > MAX_SECONDS:
                    raise TimeoutError("Research build exceeded one hour")
                inputs = read(subject)
                encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                sha = hashlib.sha256(encoded).hexdigest()
                source_hashes[subject["instrument_id"]] = sha
                out.execute("INSERT INTO source_inputs VALUES (?,?,?,?)",
                            (subject["instrument_id"], subject["symbol"], sha, zlib.compress(encoded)))
                windows = make_windows(inputs, sessions, allow_unverified_basis=allow_unverified_basis)
                for window in windows:
                    out.execute("INSERT INTO windows VALUES (?,?,?,?,?,?,?,?,?)", (
                        window["window_id"], subject["instrument_id"], subject["symbol"],
                        window["effective_date"], window["announcement"], window["reported_period_end"],
                        window["forward_eps"], window["status"], json.dumps(window, sort_keys=True, allow_nan=False)))
                counts = Counter()
                rows = 0
                numeric = 0
                for row in daily_series(inputs, sessions, windows):
                    rows += 1
                    numeric += row["forward_pe_proxy"] is not None
                    counts[row["status"]] += 1
                    out.execute("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?)", (
                        subject["instrument_id"], subject["symbol"], row["trade_date"], row["close"],
                        row["forward_eps"], row["forward_pe_proxy"], row["status"], row["window_id"],
                        row["price_version_id"]))
                statuses.update(counts)
                summary.append(dict(symbol=subject["symbol"], rows=rows, numeric_pe=numeric,
                                    windows=len(windows), statuses=dict(counts)))
                out.commit()
                if staging.stat().st_size > 8 * 1024 ** 3:
                    raise ValueError("Research output exceeds the 8 GiB bound")
                if progress:
                    progress(index, len(selected), summary[-1])
            metadata = dict(contract=VERSION, label=LABEL, cutoff=cutoff, start=start, end=end,
                            period_matching_policy=PERIOD_MATCHING_POLICY, ratio_policy=RATIO_POLICY,
                            estimate_period_policy=ESTIMATE_PERIOD_POLICY,
                            is_point_in_time=False, allow_unverified_basis=allow_unverified_basis,
                            source_coherence="per_instrument_company_market_lock_cohort",
                            calendar="XNYS", announcement_date_only_policy="next_session",
                            quarter_date_match_tolerance_days=7, quarter_length_bounds_days=[70, 110],
                            observed_long_quarter_bounds_days=[111, 119],
                            corroborating_filing_max_lag_days=7, corroborating_transcript_max_offset_days=14,
                            source_hashes=source_hashes, summary=summary, statuses=dict(statuses),
                            implementation_sha256={
                                str(p.relative_to(Path(__file__).resolve().parents[2])): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (Path(__file__), Path(__file__).parent.parent / "company" / "forward_pe.py")},
                            session_calendar_sha256=digest(sessions))
            out.executemany("INSERT INTO metadata VALUES (?,?)",
                            [(k, json.dumps(v, sort_keys=True, allow_nan=False)) for k, v in metadata.items()])
            # Preserve the actual calendar used, including ad-hoc holidays and early closes.
            out.execute("INSERT INTO metadata VALUES ('sessions',?)", (json.dumps(sessions),))
            out.commit()
            if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or out.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Research artifact integrity failed")
        # No overwrite, including another process publishing the same name.
        import os
        os.link(staging, output)
        staging.unlink()
        return metadata
    except BaseException:
        # A partial file is deliberately retained for diagnosis, never presented as complete.
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", help="Retained tickers; default is all with quarterly estimates")
    parser.add_argument("--start", default="1980-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--allow-unverified-basis", action="store_true",
                        help="Explicitly include research ratios with unresolved currency/share basis, visibly flagged")
    args = parser.parse_args(argv)
    if args.symbols and any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", s) for s in args.symbols):
        parser.error("Use uppercase provider ticker symbols")
    cutoff = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    end = args.end or cutoff[:10]
    if not "1980-01-01" <= date.fromisoformat(args.start).isoformat() <= date.fromisoformat(end).isoformat() <= cutoff[:10]:
        parser.error("Dates must be ordered between 1980-01-01 and today")
    stores = fixed_stores()
    selected = subjects(stores, args.symbols, cutoff)
    if not selected:
        parser.error("No retained quarterly estimate subjects")
    selected = attach_sharadar_links(stores, selected, cutoff)
    sessions = session_calendar(end)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite"
    output = PROJECT_ROOT / "exports" / "forward-pe" / name
    print(json.dumps({"state": "building", "symbols": len(selected), "output": str(output),
                      "provider_requests": 0, "cutoff": cutoff}), flush=True)
    def progress(index, total, item):
        if index % 25 == 0 or index == total or total <= 10:
            print(json.dumps({"completed": index, "total": total, **item}), flush=True)
    result = materialize(output, selected,
                         lambda subject: read_inputs(stores, subject, cutoff, args.start, end), sessions,
                         cutoff=cutoff, start=args.start, end=end,
                         allow_unverified_basis=args.allow_unverified_basis, progress=progress)
    print(json.dumps({"state": "complete", "output": str(output),
                      "symbols": len(selected), "statuses": result["statuses"]}), flush=True)


if __name__ == "__main__":
    main()
