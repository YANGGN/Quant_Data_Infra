# Daily derived-data refresh

Status: implemented and activated September 21, 2026. The initial retained-data
run completed; dated evidence is below and in the operating envelope.
The earlier audit and recommendation are retained as decision context.

## Recommendation

Use one local derived-data runner with two Toronto-time slots:

- **23:30 Monday–Friday:** main refresh for the latest completed US trading session.
- **06:30 daily:** catch up late company inputs, the 05:00 Sharadar refresh,
  and already-published historical price repairs. Skip computation when there
  are no relevant input changes; do not invent weekend trading sessions.

The clock is a starting point, not evidence of fresh inputs. Check publication
receipts and source versions for each calculation/ticker. A partially successful
collector should not block unrelated tickers. A running, interrupted or failed
input must remain explicitly stale or unavailable. Do not invoke or retry
collectors from the derived job.

If only one daily slot is desired, choose **06:30 Toronto time**. The second
slot gives useful same-evening results. Existing WSL host-wake coverage must
include these slots when implementation is activated.

## Input labels — September 23, 2026

Status and Forward P/E distinguish input freshness from calculation completion.
**Current** includes an older retained capture when a validated recent source
check confirms unchanged values. **Stale** requires a successful publication
of changed input data, captured before the calculation cutoff, which is newer
than that calculation's recorded input capture. An older timestamp by itself
is **Freshness unconfirmed**, not stale. Failed, absent, empty, future or
unverifiable source-check evidence cannot establish that an old capture is
current. Other input warnings, waiting inputs and calculation catch-up remain
separate.

Labels describe the selected publication and its cutoff. Later source checks
cannot retroactively make an earlier publication current or stale. If detailed
evidence has been pruned, summary counts remain explicitly unconfirmed; a
missing publication receipt leaves the combined historical count unclassified.
Original run receipts, timestamps, derived values and public tool contracts
are preserved. The presentation reads retained evidence only.

Publication receipts embedded in SQLite and saved as JSON use the same strict
numeric parser for comparison, including fractional elapsed seconds. This
keeps integrity checks while avoiding false receipt mismatches.

## Observed inputs and timings

The installed price, company, selected-company and Sharadar timers were enabled.
Recent durable run receipts were read for ten days; 285 records were available,
with no invalid records or truncation.

| Input | Installed start, Toronto time | Observed recent completion |
| --- | --- | --- |
| Daily prices | 18:00 weekdays | About 18:18 |
| Macro current inputs | 18:30 weekdays | About 18:34; several partial source failures |
| Company actions/estimates | 19:00 weekdays | About 19:20–19:24; partial results |
| Selected earnings/statements/quarterly estimates | 20:00 weekdays | September 16 at 23:17:36; September 17 at 23:01:33 |
| Sharadar fundamentals | 05:00 daily | September 21 at 05:08:45 |
| SEC submissions/conditional facts | 07:15 weekdays | September 21 at 07:54:35; partial results |
| Weekly price repair | Saturday 02:00 | Separately scheduled; consume its published corrections |

An unconditional 21:00 run would precede recent selected-company completion.
The selected-company service can run for up to twelve hours, so even 23:30 or
06:30 cannot guarantee completion. Do not wait indefinitely or interpret
systemd's current inactive/success default as proof of a completed collector.

September 18 selected-company history is unconfirmed and has no result.json.
Its last progress was September 18 at 21:48:40 Toronto, with 18,159 units
pending; that historical 'running' label is not proof of a live process.
The September 18 price receipt reports 2,350 successful, seven failed and two
skipped symbols. September 16/17 selected-company terminal receipts are
processed_with_gaps: 41,787 successful and 32 failed steps each, despite exit 0.
Read terminal receipts and per-input publication state, not only process exits.
This review did not retry or repair any collector.

## What the runner should cover

| Calculation or artifact | Observed behavior | Recommendation |
| --- | --- | --- |
| Forward P/E, next-four-quarter EPS and quarter-to-announcement links | Stored manual research snapshot in exports/forward-pe | First nightly calculation; update newly completed sessions and affected inputs |
| Derived coverage/freshness | Snapshot counts and warnings exist; no recurring P/E refresh | Publish latest price/estimate cutoffs, run time, missingness counts, and waiting/partial status with each refresh; expose these on Status |
| Market returns, ETF momentum/volatility, statistical analytics | Read-time or pure calculation paths; no separate materialized tables found in the audited stores | Keep on demand; no nightly duplicate calculation required |
| Fundamental net margin and liabilities/assets | Calculated by the versioned reader from retained same-period facts | Keep on demand; provider-supplied ratios continue through their existing collectors |
| News search index and SEC filing-to-issuer membership | SQLite insert trigger and SQL view respectively | Already maintained by ingestion/querying; no blanket nightly rebuild |
| Option IV/Greeks and diagnostics | Provider capture retains IV/Greeks; existing options collection/analytics path | Keep the existing options workflow; do not recompute historical captures nightly |
| Luna transcript outputs | Saved derived outputs with source lineage | Separate new/changed-transcript queue; never rerun the entire history as part of P/E maintenance |
| transcripts.sqlite / transcripts.zip | User-requested sharing copies | Keep as deliberate exports, not automatically changing operational outputs |

The proposed 02:00 incremental Equibles timer is not installed. The legacy
transcript timer is present; this is not proof of a functioning new-transcript
plus Luna extraction pipeline. New transcript acquisition and model execution
need their own defined incremental scope, separate from this local math job.

Trailing P/E, price/sales, EV/EBITDA, forward revenue and estimate-revision
dashboards are possible later extensions, not existing local daily
materializations found by this audit. Do not expand the initial implementation
until their definitions, price/share basis and consumer need are settled.

## Small implementation scope

1. Start with the existing forward-P/E calculator and its current 2,182-ticker
   saved universe. Reuse fixed source stores, coordinated physical locks and
   established readers; perform no provider calls.
2. Append new trading sessions and recheck recent sessions for corrected prices
   or late publication. Propagate older price corrections only to the exact
   dates identified by repair/publication evidence; a five-day lookback alone
   would miss the historical fixes already encountered.
3. Preserve the existing reconstructed-history baseline and per-day source
   cutoff/version lineage. For ongoing observations, record the actual
   calculation and estimate capture times. The 23:30 or following-morning
   calculation is not an assertion of consensus known at the market close.
   Do not apply today's estimates across the entire old history every night;
   separate justified historical corrections from newly captured estimates.
4. Calculate only changed tickers/windows. Publish a compact serving snapshot
   after row/date/component/arithmetic and missingness validation, then
   atomically update the dashboard pointer. Preserve the last good snapshot on
   failure. Avoid a new full 6+ GB compressed-source/history copy each day.
5. Record terminal per-ticker outcomes, input freshness and last successful
   publication. A new price may use the last available estimate only when the
   quarter window is still valid and the older estimate cutoff is visible.
   A newly announced but unmappable quarter must stay unavailable.
6. Keep one writer, one finite run, explicit catch-up bounds and deterministic
   replay. Host downtime catch-up should process missed completed sessions
   within those bounds. Activating schedules also needs the corresponding
   existing host-wake integration, with no collector schedule changes.

The original read-only audit covered all four operational schemas through coordinated
quiet immutable reads, current systemd timer/service metadata, retained run
receipts, the forward-P/E implementation, and relevant public calculation
paths. That original audit changed no code or service behavior and required no tests;
the subsequently approved implementation and validation are recorded below. Private evidence is in
.local/derived-refresh-review-20260921/.


## Approved implementation contract — September 21, 2026

The user's “please implement” approves the two proposed clock slots and the
corresponding Windows host wake slots (23:28 weekdays, 06:28 daily). This adds
one derived runner, not a provider collector. Initial validation includes one
finite local run across the saved 2,182-ticker roster, at most 2,500 targets,
20 missed sessions per ticker per run, 45 minutes of refresh work, and zero
provider requests. Source preparation uses existing bounded readers; the
service has a 46-minute hard stop and no automatic restart.

The saved historical artifact is the pinned immutable baseline. A compact
SQLite overlay stores only appended daily rows, exact changed-price overrides,
used windows, source-version lineage, per-ticker check state and coverage.
Each publication preserves the baseline and original estimate denominator on
already-published days. New observations are labelled daily captures and
retain their calculation cutoff; they do not assert market-close point-in-time
availability. An observed unmappable new earnings release still blocks a new
day's ratio. New ratios also become unavailable when the first forward quarter
is more than 120 days past its period end, matching the conservative reporting-lag
bound. Published historical observations are preserved. Failed source cohorts preserve all previously published values.

The runner scans current source versions but calculates only new sessions.
Historical price changes are detected by a bounded scan of current published
version identities across each saved history, including late commits whose
capture timestamps predate the previous check. Only changed dates are written.
Failed or unverified-basis cohorts do not advance their price-check watermark. Estimate and earnings capture freshness
are recorded per ticker; a partial batch or old capture is never reported as
fresh consensus solely because its process exited successfully.

The overlay is limited to 512 MiB. Publication requires SQLite quick-check,
foreign-key and numeric arithmetic checks before switching current.json.
Keep the current and previous runner-created serving copies plus the pinned
baseline; remove only redundant compact SQLite copies explicitly marked as
created by this runner. Their small completion receipts remain, and all
pre-existing/manual artifacts are outside cleanup scope. No original source
or baseline data is deleted.

The Status page gains a derived-calculations section; Forward P/E shows the
configured cadence, last check and per-ticker stale-input notes. Fixed timer
metadata and durable run receipts also feed the existing activity timeline.
Tests remain component-scoped: calculation/reader compatibility, incremental
publication and failures, schedule/DST, run receipts, and the affected UI.
Independent preactivation review is required by the new-scheduler fast path.


## Activation and first run

On September 21, the user service/timer were installed, the timer was observed
enabled/active, and its next slot was 23:30 EDT that evening. The existing
Windows host task has 16 triggers: all 14 previous triggers and its action,
principal and settings were preserved; the two approved wake slots were added.

The one initial local run completed at 12:27:14 Toronto in 799.218 seconds.
All 2,182 frozen-roster members were checked: 217 current, 1,965 with older or
unchecked quarterly estimate/earnings inputs, and none unreadable. The latest
completed session was September 18; no sessions or changed prices needed
adding. Stale cohorts comprise 287 earnings-only, 191 estimates-only, and
1,487 both. These flags do not mean the corresponding P/E histories are absent.

Publication 20260921T161355001773Z.sqlite is 1,818,624 bytes. It preserves the
12,636,373 daily rows and 7,243,982 numeric ratios in its pinned baseline.
SQLite quick-check and foreign-key validation passed. C, MU and GOOGL still
have 1,255/1,255 five-year ratios; MU retains 312 negative ratios. Provider
requests and canonical writes were zero.

The initial run's detailed publication receipt is complete. Its generic
activity-summary observer warned because partial counts were paired with exit
0. That original exit receipt and warning remain unchanged. The runner now
returns exit 2 for complete_with_gaps, as the shared recorder requires, while
retaining the successful serving publication. The existing recorder was not
changed, and the full population was not rerun. Temporary end-to-end tests
prove future partial-count summaries are persisted.

Validation comprised a 112-test component pass, an affected 51-test pass after
the live Status description correction, and a 64-test pass after partial-exit
integration (114 distinct checks across these runs). Independent reviewers
cleared the calculation/publication boundaries and final two integration fixes.
The full suite was unnecessary: no shared canonical/schema invariant changed.
Chromium verified actual series coverage, selected-day interaction, configured
cadence, running/completed Status, negative values and mobile layout without
JavaScript errors. Evidence is retained in
.local/derived-refresh-implementation-20260921/.

Existing provider units and transcript exports were unchanged. The manual
Inspector alone was restarted to load the new display. No commit or push was
performed.


## September 22 — distinguish source checks from source changes

The user requested correcting the Q/XOM issuer mappings and the misleading
“Incomplete” label after the 06:30 derived run. The new read-only presentation
uses its completed publication receipt and bounded, identity/time-bound retained
selected-company check receipts. A successful unchanged check establishes input
recency without creating a canonical capture, changing a ratio, or rewriting the
original process/run evidence. All pages in a relevant source request group must
succeed; failed, missing, mismatched, future or unfinished evidence cannot clear
a warning. Unsupported price-basis warnings remain warnings. Per-ticker labels
are pinned to the same immutable artifact as the displayed series.

For the September 22 morning publication, the UI now distinguishes 2,182
calculated symbols, 2,180 with current checks, and two freshness warnings.
The 1,946 successful unchanged checks explain most of the old 1,948 count.
Original exit 2 and run counts are preserved. “Completed · input warnings”
is separate from failed/unfinished work and from unavailable P/E values.
Pruned historical artifacts retain publication evidence but do not invent
per-ticker source-check reconciliation.

The Q and XOM mapping correction uses existing versioned market-mapping
publication, with exactly two member changes and 2,246 unchanged members:
Q → 0002058873; XOM → 0002115436. Q uses retained FMP profile evidence.
XOM uses explicitly labelled user-reviewed controlled evidence corroborated
by the retained SEC directory and existing canonical issuer; FMP's original
predecessor CIK 0000034088 remains preserved. The prior mapping remains
queryable. Only the existing selected-company activation population's mapping
references follow the new version; no timer or provider invocation changed.
The morning Q/XOM warnings remain historical facts until subsequent source
checks establish freshness.

The P/E method remains announcement-anchored estimated EPS: an actual earnings
announcement identifies the last reported fiscal quarter, and the denominator
is estimated EPS for the following four unreported quarters. Actual EPS is not
the denominator. The 207 latest-session unmapped_announcement cases have an
event that cannot be linked confidently to its fiscal period; this is distinct
from having no announcement or no estimates. The dashboard method disclosure
now explains that distinction. No historical ratio was recalculated.

Evidence, mapping receipt and zero-write replay, focused test logs, and browser
acceptance artifacts: .local/q-xom-derived-status-20260922/.

Browser acceptance caught a cold-reader deadline regression before handoff.
Source-check projection now runs only as an Inspector HTML annotation after the
registered analysis finishes, pinned to that result's immutable snapshot hash.
The shared public reader and its time budget are unchanged. Nonselected calendar
days use publication receipts only; selecting a day enables detailed source-check
reconciliation. This avoids re-indexing a month of collection checkpoints for one
page. Snapshot movement and no-evidence fallback have focused regressions.

## September 23, 2026 — explicitly authorized reviewed-period repair

The subsequent user decision authorizes applying reviewed event-to-period
associations in production for the 207 incomplete symbols, including labelled
most-likely decisions for the remaining uncertain events. This supersedes the
prior review-only outcome for this exact cohort. The finite operation uses
zero provider requests and zero canonical writes, with a 900-second repair
bound and the existing 512 MiB compact-publication limit. It repairs September
22 derived observations only, preserving older observations and source actuals.
One missing latest-session AGM row may be filled from retained inputs.

The established refresh publisher now has an internal, explicit repair cohort
option; its default scheduled behavior remains unchanged. Normal future runs
read the fixed event-review catalog. No timer, cadence, unit or provider run is
modified or manually triggered. Semantics and limitations are specified in
FORWARD_PE_PROXY.md under Reviewed reporting periods. Evidence and exact
production outcome are in .local/announcement-quarter-production-20260923/.
