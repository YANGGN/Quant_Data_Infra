# Daily Theta options collection and weekend gap repair

Decision: September 26, 2026. The user requested a daily ThetaData collector
populating the existing options database and a weekend repair for gaps in the
past week, following the established price repair pattern. This authorizes
the implementation of these two units for the existing 15-ETF universe.
The user subsequently explicitly approved installing and enabling both timers,
with no immediate manual fetch. The activated routine supersedes the
earlier absence of recurring Theta EOD collection; completed historical
backfills and their older unresolved gaps retain their original no-repeat scope.

## Collection and compatibility

The fixed roots are SPY, QQQ, IWM, DIA, XLB, XLC, XLE, XLF, XLI, XLK, XLP,
XLRE, XLU, XLV and XLY. No single-name universe is added.

The authoritative output remains `data/options.sqlite`. Both jobs reuse
`build_capture`, the existing checksum-checked migrations, physical store lock,
and `OptionsStore.publish`. Each capture has the same selected contract fields
(at most 300), 102 full/selected/remainder summary cells, bounded leaders,
source receipts, actual acquisition time, model/selection version and explicit
missingness as the historical backfill. There is no schema migration, historical
rewrite, new daily rollup format, or change to the dashboard reader.

Each missing symbol/session requests combined EOD Greeks (version 1, underlying
NBBO enabled) and that session's open-interest report, once each. Previous
trading-session OI effective dates come from the existing Theta calendar
interpretation. Empty EOD/OI, no usable matched OI or an unavailable valid
underlying reference remain gaps. Modern collection does not introduce a
price-only fallback or fetch from another provider. Partial contract field
coverage remains explicit under the existing model.

Theta documents generation of EOD reports at 17:15 Eastern:
[official EOD Greeks reference](https://docs.thetadata.us/operations_python/option_history_greeks_eod.html).
The daily time below provides a two-hour margin, not a guarantee of provider
availability.

## Schedule and finite bounds

| Unit | Clock in America/New_York | Window | Data-request cap | Received-byte cap | Run deadline |
| --- | --- | --- | --- | --- | --- |
| `quant-data-theta-options-daily.timer` | Weekdays 19:15 | Same local date only, calendar gated | 32 | 2 GiB | 20 minutes |
| `quant-data-theta-options-weekly.timer` | Sunday 03:00 | Monday-Friday preceding the latest due Sunday | 152 | 8 GiB | 60 minutes |

The caps include up to two annual calendar requests across a year boundary.
There is one active data request at a time and zero automatic request retries.
The existing credential resolver and pinned Theta SDK 1.0.11 are reused.
Services have 21/61-minute hard timeouts, no restart, 2 GiB memory ceilings,
a 25 GiB options-store cap and 20 GiB free-space reserve. SDK authentication
uses the established credential mechanism and precedes bounded data RPCs.

The daily timer is nonpersistent; missed dates are handled by the weekend
audit. The weekend timer is persistent like the price repair timer: missed
host time attempts only the most recent due week, never every missed week.
Both require the Ubuntu user scheduler to be running. An activation file pins
the policy digest and first eligible slots, preventing installation from
dispatching an earlier period. The earliest eligible repair date is
September 21, 2026. Ordinary weekly windows advance with the clock.

The first verified slots are Sunday September 27, 2026 at 03:00 Eastern
(covering September 21-25), and Monday September 28 at 19:15 Eastern.
Both timers are enabled and active/waiting as of the activation receipt below.

## Preservation and failure handling

The initial audit reads the existing store under its physical lock using the
approved immutable reader. Network work and staging occur after that lock is
released. Existing symbol/session captures are skipped before acquisition.
The new opt-in `missing_only` publication policy also checks under the writer
lock, preserving a capture if another publisher filled it after the audit.
Default correction and semantic-replay behavior for existing callers is unchanged.

A separate shared job lock serializes the two scheduled jobs. The durable
period-start marker precedes authentication and provider work. Its contents,
rename and every parent directory entry through the project root are fsynced
before authentication; a flush failure makes zero requests. A completed
period returns its retained result without requesting data again. An interrupted
period requires explicit reconciliation; restarting cannot reset its request
allowance. A failed daily period may be attempted once by the separately
authorized weekend repair if that session is still missing. There is no further
automatic pass within that week.

Known transient provider failures are recorded as gaps while other symbols
continue. Authentication, permission, unknown schema/identity failures,
resource caps and storage errors stop the run. Empty or unusable responses
never create placeholder captures. Unresolved periods exit nonzero.

Temporary source responses use the established staging mechanism. A published
capture is verified via a separate immutable read before its exact source
files are removed. Failed units retain staged evidence. A publication race
preserves existing canonical data and retains unmatched staged evidence.
There is no broad cleanup of old backfill roots.

Receipts, plans, request admission, stage files and per-period results are in
`data/.operations/theta-options/{daily,weekly}/<period-end>/`; top-level
`daily-latest.json` and `weekly-latest.json` expose the last terminal results.
The existing monitor's scheduled history copy can pick up the newly appended
capture IDs. This change does not manually run or modify that monitor.

## Impact and validation

Changed behavior: additive daily/weekly scheduling and opt-in missing-only
publication. Affected boundaries: date/calendar scope, request accounting,
restarts, physical locking, canonical capture preservation, and the existing
daily history reader. Existing historical collectors keep their defaults.

Focused checks:
`tests.options.test_theta_daily`, `test_theta_compact`,
`test_theta_parallel`, `test_theta_etf`, `test_theta_retry`,
`test_monitor_history`, and `test_monitor_history_model`.
The first complete run passed 96 tests in 16.725 seconds, with no skips.
Tests use explicit temporary stores and fake transports. The new scheduler
tests prohibit socket connections and prove provider work occurs outside the
physical SQLite lock. All four units passed systemd verification and both
calendar expressions resolved to the intended first slots.

The new services use the existing working historical runtime:
`.local/theta-discovery-20260924/venv/bin/python` (Python 3.12, Theta SDK 1.0.11).
The separately configured monitor runtime lacked `python-dotenv` during the
read-only import check; it was not modified by this task.

Independent review passed after correcting directory durability of the
period-start marker. The affected scheduler suite passed 26 tests in 8.686
seconds, including two new durability cases; the other 72 options/reader tests
retain their passing result. This is 98 distinct passing tests, with no skips.
The reviewer inspected source and retained test evidence and independently
checked pure clock cases; the full tests were not independently rerun.

The immutable live preflight passed registry/migration checks and all 15 latest
capture-shape checks. All 15 ETFs end on September 23, 2026. Source high-water
is 37,863 and file size is 8,671,899,648 bytes. File/sidecar stamps were unchanged
and available space exceeded the 20 GiB reserve. No provider call occurred.

Installation/enabling was rejected by automatic approval review before command
execution: it interpreted implementation as insufficient explicit authorization
to activate recurring units under the project scheduler rule. At that checkpoint no new units were installed, enabled or run, and no
operational activation file was created. The user then explicitly replied
"Install and enable both timers." That later approval authorized activation.


## Verified activation

At 2026-09-27T03:20:07Z (September 26, 23:20 Eastern), both new timers were
installed, enabled and active/waiting. Their verified next triggers are Sunday
September 27 at 03:00 EDT and Monday September 28 at 19:15 EDT. The activation
record pins those first slots and the independently reviewed source hashes.

Both service execution timestamps and timer last-trigger fields are empty.
No service was manually run, no provider fetch occurred, and canonical
file/sidecar stamps remained unchanged. This proves schedule activation, not
a successful live provider collection. Ordinary first-run results will be in
the period receipts and systemd journal. WSL must be running for timely daily
execution; weekly persistent catch-up remains limited to the latest due week.

Evidence: .local/theta-daily-20260926/{focused-tests.log,correction-tests.log,
independent-review.md,preflight.json,activation.json}. The task changed only its
new scheduler/entry point/units/tests, the opt-in options publisher argument,
the additive companion registry declaration, and the relevant operational docs.
Existing uncommitted work was preserved; nothing was committed or pushed.

## Live fetch Status integration — September 27, 2026

The user's follow-up request connects the two existing Theta jobs to the local
Inspector's run calendar. The active timer bindings are daily at 19:15 Eastern
and Sunday repair at 03:00 Eastern, with their three companion-registry Theta
datasets and explicit source `data/options.sqlite`. The retired Alpaca timer is
no longer an active binding; its old run receipts remain readable as historical
evidence. Neither the collector policy nor any installed unit is changed.

The normal daily/weekly CLI forms now use the existing best-effort run observer.
Future invocations save their original exit result and bounded numeric summaries.
Counts use ETF sessions (one symbol/date): published, unresolved gaps, already
present and unfinished. A stopped run's unfinished count does not prove whether
a request was attempted. Partial Success requires recorded completed work.
Early failures, all-present audits without calendar counts, and cached replays
leave counts unavailable instead of inventing totals or counting an old
publication again. Receipt failures do not alter the collector outcome.

Existing runs without these new receipts still use the established systemd
completion evidence. The September 27 weekly run is displayed as Completed;
its original native period result records 30 publications and no unresolved
gaps, but no per-invocation summary is retroactively manufactured. The next
daily slot is September 28 at 19:15 EDT. These observations made no provider
requests and opened no canonical store.

The Inspector's strict registry guard is aligned with the already accepted
2.93.0 registry. Theta run details link to the existing agent-tool page because
the optional options datasets are outside the legacy four-store status table;
no link or fallback points at Alpaca options data. The existing calendar and
detail layout is reused.

Validation and local reload evidence are retained under
`.local/theta-fetch-status-20260927/`. This change does not install, restart,
manually trigger, enable or disable any collector or timer.

Final evidence: 73 distinct focused tests passed (69 schedule/receipt/presentation,
three Status route checks and one current tool-manifest compatibility check).
The initial positional-calendar expectation and old 2.92.0/84-tool expectations
were corrected to the current bindings and 2.93.0/90-tool catalog. Desktop
1440px and mobile 360px Chromium checks passed for Theta completion, source,
disclosure, tool link, horizontal bounds and browser exceptions. The in-app
browser tool could not initialize, so the installed native Chromium was used.

Only the existing manual loopback Inspector on port 8766 was reloaded; its
health and actual Status response returned HTTP 200. The first Status client
timed out after 30 seconds; the subsequent bounded check completed in 9.91
seconds. The collector request policy, installed units and canonical data were
unchanged. The optional-store status table itself remains outside this update;
Theta run details route readers to the already available agent tools.
