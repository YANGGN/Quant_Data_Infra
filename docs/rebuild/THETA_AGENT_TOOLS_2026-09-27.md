# Theta options tools for local agents

The user approved implementation of all six proposed tools. They are exposed
through the existing local `bin/quant-data-tools` interface at **1.0.0**,
registry **2.93.0**, versioned schema catalog **2.37.0**. Each reads only the
host-selected `data/options.sqlite`, through `OptionsStore` and its physical
lock / immutable descriptor procedure. There are no provider requests, caller
paths, SQL, write connections, Alpaca fallback, or monitor-store reads.
This is local command access; it does not deploy a server.

The legacy four-store registry is unchanged in ownership and migrations.
These optional-domain adapters have empty legacy `stores` / `datasets`
bindings; the description and versioned `theta_source` record declare their
actual **options** store role and Theta datasets. They do not label the source
as market. The exact predecessor projection restores registry 2.92.0 bytes and
all older tool contracts, variants, defaults, and dataset declarations.

## Calls

Other agents use the absolute launcher from any working directory:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe options.get_daily_snapshot --tool-version 1.0.0
```

Send a strict JSON envelope to the launcher's `call` command on standard input:

```json
{"api_version":"1.0","tool":"options.get_daily_snapshot","tool_version":"1.0.0","arguments":{"symbol":"SPY","session":"2026-09-23"}}
```

| Tool | Required arguments | Optional arguments / output |
| --- | --- | --- |
| `options.get_coverage` | `start_date`, `end_date` | `symbols` defaults to all 15 ETFs. First/latest retained session at cutoff, total sessions, requested coverage, gaps, capture freshness, latest volume/OI/IV quality and source coverage. |
| `options.get_daily_history` | `symbol`, `start_date`, `end_date` | Chronological daily rows with call/put volume, OI and effective date, volume/OI put-call ratios, concentration, quote quality, 0DTE volume share, 7/30/90-day ATM IV. |
| `options.get_daily_snapshot` | `symbol`, `session` | One coherent capture, daily projection, all 102 full/selected/remainder summary cells, and at most 40 saved leaders. |
| `options.get_volatility_profile` | `symbol`, `session` | `lookback_sessions` 2–252 (default 252), `min_observations` 2–window (default min(20,window)). ATM IV, term differences, interpolation inputs, retained ATM anchors, moneyness skew, prior-window IV rank/percentile and sample lineage. |
| `options.screen_activity` | `session` | `symbols` defaults to all ETFs; `lookback_sessions` 2–60 (default 20), `min_observations` as above; `rank_by` = relative_volume (default), put_call_change, zero_dte_volume_share, or atm_iv_30_change. All symbols use the explicit same session; missing symbols remain visible with null ranks. |
| `options.get_selected_contracts` | `symbol`, `session` | Filter by `expiration`, `right` C/P, positive decimal-string `strike_min` / `strike_max`. Returns all matching retained details, quotes, volume/OI, source Greeks and selection reasons; at most 300. |

All calls accept optional timezone-aware `as_of`. It is a **local capture-time
cutoff**, not proof of historical public availability. The cutoff cannot exceed
the host clock. Without it, the host clock is the cutoff. The research temporal
metadata records that effective instant in `as_of` mode, including for an
implicit host-clock cutoff, with `local_capture_time_only` availability and an
inclusive session-date policy. Unrepresentable timestamp boundaries are invalid
requests. For each symbol/session,
the last published eligible revision is selected, preserving earlier revisions
when a later correction exceeds the cutoff. Capture timestamps are compared as
aware instants, including microseconds and offsets. Session dates beyond the
cutoff's Eastern calendar date are excluded.

Date intervals are inclusive and limited to 366 calendar days. There is no
silent paging or row truncation: narrow the interval if a budget rejects a call.
Each response is capped at 1,000 records / 8 MiB, SQL at 5 million virtual-machine
steps and the existing host deadline. Reads bound capture headers (50,000),
each compressed/decoded payload (1 MiB), total decoded material (128 MiB),
and every detail/summary/leader query. The source lock wait is at most one second.

## Response and lineage contract

Results use the established `QueryResultV1` record/field envelope. Numeric
scalars use the platform's decimal-safe encoding. Fields ending in `_json`
contain structured JSON; parse them once, retaining the original response and
receipt. These are typed daily records, not a substitution into the older
four-store `TimeSeries` lineage enum; `series` is empty.

Every response starts with `theta_source`, the `options.theta.source@1.0.0`
domain lineage declaration. It identifies provider `thetadata`, store role
`options`, logical source `data/options.sqlite`, three existing optional
datasets and versions, capture cutoff, source high-water ID, selection rule,
derivation version, gap basis and unestablished historical point-in-time status.
No host absolute path or credentials are returned. The research analysis ID
binds the normalized query, derivation version, returned source-record digest
and explicit immutable-read / local-capture policy. Repeating the same cutoff
and unchanged source gives the same ID; a changed contributing capture changes it.

| Record | Source identity |
| --- | --- |
| `theta_daily`, `theta_volatility`, available `theta_activity` | `source_capture_id`, `source_semantic_sha256`, `source_captured_at`, symbol and session |
| `theta_capture` | `capture_id`, `semantic_sha256`, `captured_at`, symbol and `session_date`; coverage/selection/policy JSON |
| `theta_summary_cell`, `theta_activity_leader`, `theta_selected_contract` | `capture_id` referencing the response's `theta_capture` |
| `theta_coverage` | `latest_capture_json`, `latest_quality_json`, `latest_capture_coverage_json` |
| Derived rolling rows | `baseline_lineage_json` lists every contributing saved capture, including null observations |

Structured summary `stats_json`, detail `eod_greeks_json`, policy, reasons and
coverage fields preserve the existing compact database shapes. Full, selected
and remainder populations stay distinct. The leader list is a bounded reference
list, not a reconstruction of the full chain.

Missing data returns null values / explicit missing records; an unavailable or
unsafe store returns `store_unavailable` (physical lock contention can return
the existing lock error). Historical status remains `not_established`.
Collection status is explicitly `not_queried_read_only_data_evidence`: coverage
does not claim that a timer or provider run succeeded.

## Calculation semantics

- The existing `options_daily_chart_v1` projection supplies daily activity,
  OI dates and ATM IV; additional analytics use `theta_agent_analytics_v1`.
  Missing volume/OI components yield null totals and ratios. Zero denominators
  yield null ratios. OI's effective date remains separate from the session.
- ATM IV uses same-strike call/put retained `anchor:1` pairs, usable two-sided
  quotes and same-session underlying references. It averages the pair's IV.
  7/30/90-day IV uses exact terms or bracketing **total-variance interpolation**,
  with no extrapolation. Outputs include brackets and candidate anchor
  moneyness; distances are descriptive, not verified full-chain coverage.
- Term slopes are IV differences (30 minus 7, and 90 minus 30), not annualized
  regression slopes. IVs and differences are decimal fractions.
- Skew is the same-expiry 95%-moneyness put IV minus 105%-moneyness call IV.
  Each anchor must be within 0.025 moneyness of target and pass saved quote/IV
  quality checks. Choose the paired expiry closest to 30 days from DTE 1–60
  (shorter DTE breaks ties). Return actual moneyness, IDs, IVs and DTE. It is
  neither 25-delta skew nor a guaranteed 30-day interpolated skew.
- Rolling baselines exclude the current session. The window uses the last N
  sessions observed anywhere in the saved 15-ETF universe within 3N+20 calendar
  days. Missing symbol captures occupy a slot and are reported; they are not
  replaced by older observations. Null metric values do not count toward the
  minimum. Actual baseline dates, capture and usable-value counts are returned.
- 30-day IV rank = 100 × (current − prior minimum)/(prior maximum − prior minimum).
  It is deliberately unclipped and can exceed 0–100. A constant baseline gives
  null rank. Percentile = 100 × count(prior IV ≤ current IV)/valid prior count;
  ties count fully. Insufficient samples or missing current IV give null.
- Relative volume = current total / mean of valid prior totals after the minimum
  sample gate. Put/call and IV changes use the immediately prior **universe**
  session; if that symbol/date is missing, the change is null.
- 0DTE share uses full-population DTE-zero volume divided by full daily volume,
  even though 0DTE contract details are not retained. Call/put HHI and top-five
  shares remain separate side-level statistics.
- Screens rank descending, symbol ascending to break ties. Null scores have no
  rank. Activity does not establish trade direction, opening/closing intent or
  dealer positions.
- Gaps use the union of saved ETF session dates. A missing symbol on an observed
  universe session is explicit; weekdays with no ETF observations are separately
  labeled `unobserved_weekdays_calendar_unverified`. This detects possible
  whole-universe outages without labeling exchange holidays as confirmed gaps.

## Compatibility and operating scope

The older `options.search_captures@2.0.0`,
`options.search_contracts@2.0.0` and `options.get_surface_snapshot@2.0.0`
remain legacy Alpaca archive contracts. Older fixture contracts also remain.
They are excluded from the new workflow and never used as fallback.

The Alpaca options timer retirement already completed under the prior decision.
This tool implementation changes no recurring unit, canonical table, migration,
provider subscription, stored population, or monitor behavior.

Validation evidence is recorded after execution in the handoff and
`.local/theta-agent-tools-20260927/`. Tests use temporary options stores,
explicit missing legacy stores, and blocked network calls. Relevant gates are
domain calculations/source isolation, existing daily projection, public
manifest/describe/call and receipts, exact predecessor/frozen contracts,
generated-output checks, and an independent review of the stable integration.


## Completed validation

The integrated change passed **56 distinct focused tests**: 20 new Theta-tool
tests, seven existing daily-reader tests, and 29 catalog/CLI/legacy-options
tests. Earlier failures were a test helper's decimal encoding and an expected
inventory-count assertion; both were corrected and their affected checks passed.
The generated bundle check and exact predecessor/frozen-schema checks passed.
All 84 previous tool entries and 238 previous schema contracts are unchanged.

The fresh verifier found two issues: inconsistent temporal availability
metadata and unhandled overflow at extreme timestamp boundaries. Both were
corrected. Five affected primary regressions passed; the verifier independently
ran two isolated guarded tests and approved the stopped final baseline with no
remaining blockers. Full-suite, provider and scheduler checks were not run
because this additive read-only change does not alter those behaviors.

All six tools were exercised through the absolute launcher from another working
directory against saved Theta data. Coverage initially hit the host deadline;
the inventory query was tightened and all-15-ETF coverage then passed. Saved
reads returned coverage, daily history, snapshots, volatility profiles,
activity screens and selected contracts. The final snapshot also confirmed the
corrected temporal metadata. Database inode/size/mtime/ctime stayed unchanged;
no provider request or canonical write occurred.

Evidence: `.local/theta-agent-tools-20260927/validation.json`, the domain,
interface, final and review-correction test logs, manifest and six explicit
descriptions, saved-source validation records and the reviewed final snapshot.
