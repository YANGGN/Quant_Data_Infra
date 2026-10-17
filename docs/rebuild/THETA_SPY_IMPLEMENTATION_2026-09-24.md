# SPY compact options implementation

Status: Completed and verified; all 2,696 planned SPY sessions are saved.
Date: 2026-09-24

The user authorized SPY-first implementation and disposal of unneeded source
details after aggregate publication. ADR 0013 records the scoped retention and
optional-domain registry decisions.

Implementation:
- quant_data/options/model.py: fixed anchors, round/activity additions, 300-contract
  ceiling, 102 summary cells and at most 40 leader references, exact strike
  identity, explicit missingness and OI availability.
- quant_data/options/transport.py: isolated pinned Theta SDK, secure fixed hosts,
  bounded requests/bytes/deadline, no transport retries, decimal protobuf prices,
  sanitized receipts and suppressed authentication logging.
- quant_data/options/store.py and config/options_registry.json: private registered
  options.sqlite, append-only facts, current pointers, checksummed migration,
  physical-path locking, immutable reads, verified atomic publication, backup and
  restore into absent destinations.
- quant_data/options/job.py: finite SPY manifest, retained request attempts,
  restart-safe local cache reuse, explicit gaps, publication-before-cleanup,
  durable progress and completion backup.
- quant_data/options/reference.py: retained local SPY close references obtained
  through market.get_price_series@2.0.0 with metadata/lineage preserved.
- quant_data/operations/theta_spy_backfill.py: fixed private manual entry point;
  no caller-chosen SQL, database, symbols or scheduler.

The initial broad-chain sample for 2026-09-23 contained 12,206 EOD/Greeks rows
and 12,066 OI rows. Offline processing retained 262 detailed contracts, 102
summaries and 40 leader references; sample shares were 8.57% of observed volume
and 12.16% of known OI. 0DTE contributes to summaries, not initial detail.

2012 price access was denied. 2016-01-04 EOD returned 3,870 rows, but both tested
historical Greek model-input variants returned no data. Stock EOD for that date
was denied. These outcomes do not establish a named subscription tier. Older
price-only sessions preserve absent IV/Greeks and the labelled local reference
proxy; no option Greeks are fabricated.

Validation impact: additive options domain, its source decoder and private
publication/recovery boundary. Focused checks in tests.options.test_theta_compact
use temporary stores only. Shared four-store engines, validators, public
contracts and applied migrations are unchanged, so there is no shared-core
algorithm trigger for a full unrelated suite. Fresh independent review remains
required for the new writer/retention boundary.

Current primary check: 21 focused tests passed, including exact decimal decode,
selection and population reconciliation, future OI exclusion, conflicting rows,
reference-basis rejection, rollback, no-write replay, A->B->A, immutable history,
migration tamper, quiet-reader checks, no retries, health, backup and restore.
Operational results and independent findings will be appended after execution.

Known limits: exact SPY root rather than verified deliverables; chain catalogue
not independently reconciled; report-time liquidity rather than intraday depth;
historical current-provider vintages rather than original availability; deliberate
loss of full-source replay following compact publication.

## Independent verification and launch

The fresh reviewer reproduced three blockers before live publication: future
price/model-input timestamps, stale pre-lock attempt state allowing a duplicate
request, and crash-after-commit cleanup omission. Corrections were integrated,
then the reviewer confirmed all three fixed against a stopped, stable baseline.
Both primary and independent focused runs passed 26 tests with no skips.
The recheck used temporary stores/fake transports only. Retained 2016 and 2026
provider samples also passed the corrected transform without new provider calls.

Calendar preparation finished with 19 cumulative current-run data requests and
12,275,149 received bytes. The manifest contains 2,696 sessions, each with a
prior trading date and a retained versioned local price reference if needed.
The finite manual process launched on 2026-09-24 under native WSL Python,
PID 137553, using the isolated pinned SDK environment. Live progress is written
to .local/theta-spy-20260924/status.json and fetch.log; PID and counts in this
receipt are dated observations, not proof of continued process state.

## First live publication evidence

At the first immutable-read verification, 5 sessions
were present (2026-09-17 through 2026-09-23),
with 1352 selected rows and
510 summary cells. SQLite quick_check was ok and
foreign-key violations were zero. The first capture contained 262 detail rows,
102 summary cells, 40 leader references and two source receipts; its exact broad
source artifacts had been removed. Evidence is retained in
.local/theta-spy-20260924/first-publication-verification.json.

Status snapshot at 2026-09-24T15:26:29.616262+00:00: running,
10/2696 sessions published,
0 recorded gaps, 3194880 database bytes,
38 cumulative current-run data requests and
127973046 received bytes. Full history acquisition and the
completion backup are still pending. The process continues independently of the
chat response; status.json is the authoritative later progress snapshot.

## Parallel acquisition upgrade - 2026-09-24

The user explicitly requested parallel downloads after the sequential run's
measured bottleneck was reported. The upgrade uses four downloader threads,
at most four pending sessions, and the existing single coordinator for all
transformations, SQLite writes, readback and cleanup. One authenticated SDK
client/channel is shared; per-call response digests, byte counts and message
counts are thread-local. Request reservations and aggregate received bytes are
synchronized. Durable attempt claims and receipt appends have one journal lock;
only the coordinator writes status.json. No provider request is retried.

An acquisition failure now stops admission immediately, drains already-admitted
requests into their recoverable caches, and records the original failure. Input
validation retains the established explicit-gap behavior. Temporary response
compression uses level 1 to reduce CPU overhead; retained canonical compression
and semantic fingerprints are unchanged. Frozen manifest, 20,000 requests,
128 GiB and the original 24-hour clock are unchanged. No schema/model/registry
migration or recurring unit changes.

The legacy process was handed over at 18:07:59 UTC with 1,122 sessions committed.
A physical-store lock held publication while the next day's two complete input
artifacts and hashes were verified. The old process was stopped only then,
with no provider request in flight; the pending 2022-04-01 session can be
published from cache without another request. The handover made zero provider
calls. Evidence: .local/theta-spy-20260924/parallel-handover.json.

Validation impact: options-only request accounting, parallel acquisition,
recovery and its serial publication consumer. Primary checks:
tests.options.test_theta_compact and tests.options.test_theta_parallel,
34 tests passed with no skips. New regressions cover global cap contention,
interleaved response receipts, duplicate-unit exclusion, bounded prefetch,
failure draining, cached resume, equivalence to serial transformations, and
single-threaded publication. Independent review and parallel live restart are
recorded below when completed.

Theta's current concurrency documentation lists four concurrent requests for
Standard and eight for Pro, account-wide:
https://thetadata.net/docs/Articles/Data-And-Requests/Concurrent-Requests.html
Historical access to 2016 and Greeks is consistent with at least Standard
according to its access table, but the numeric account tier is not relabelled
as independently verified. Four is the local hard ceiling; throttling/error
stops acquisition without retry. SDK method inspection showed request-local
arguments/protobufs and read-only use of the authenticated identity.

Independent review found one adjacent failure-path blocker: the former broad
ValueError handler could treat post-commit cleanup failure as a recoverable
validation gap, admit another day and overwrite its pending cleanup journal.
Only build_capture validation is now recoverable; preparation, publication,
readback and cleanup failures stop the coordinator and drain pending downloads.
The new regression preserves the original journal and retained sources.

Primary and independent rechecks passed all 35 focused tests with no skips.
Source/test writers were stopped during verification. No shared-core change
triggered the unrelated full suite. Pre-restart immutable health found 1,122
sessions, 291,856 detail rows, 114,444 summary rows, quick_check ok and zero
foreign-key violations. Durable attempts contained zero duplicate unit IDs.
Global accounting resumed at 2,263 requests and 9,884,852,968 received bytes.

Parallel PID 155252 launched at 2026-09-24T18:19:19.770776+00:00 with four
downloaders and one writer. Source fingerprints and launch evidence are in
.local/theta-spy-20260924/parallel-launch.json. Live performance evidence follows.

## Parallel live verification and early performance

At 2026-09-24T18:20:41.791704+00:00, an immutable read under the physical store lock found
1135 sessions, quick_check ok and zero foreign-key
violations. The handover date 2022-04-01 has exactly one capture, its two original
request attempts and no request after handover. Global duplicate attempt IDs:
zero. Evidence: .local/theta-spy-20260924/parallel-live-verification.json.

At 2026-09-24T18:21:22.631578+00:00, the job was running with
1144/2696 sessions published,
0 recorded gaps, 339275776 database bytes and
2308 cumulative requests. The parallel segment published
22 days in 120.25 seconds:
5.47 seconds/day versus 10.83
in the preceding 50-day sequential window, an early 1.98x speedup.
Receipt intervals confirm 4 overlapping
requests. This comparison uses different date windows and includes the one
cached handover day plus startup/publication overhead; it is an observed early
throughput sample, not a guaranteed speedup or completion forecast.

Evidence: .local/theta-spy-20260924/sequential-performance.json and
parallel-performance.json. Full history and the completion backup are still
pending; the finite background process continues within the original limits.

## Final completion and verification

The finite SPY history job completed at 2026-09-24T20:00:18.700484+00:00
(16:00 Toronto time on September 24). Post-completion immutable reads verified
all 2696 planned sessions, 2016-01-04 through 2026-09-23, with
666581 detail observations, 274992 summary
cells, 213276 distinct selected contracts and 107840 minimal
leader references. No session gaps were recorded. Main database and backup
quick_check passed, and both had zero foreign-key violations.

The options database is 747794432 bytes; the verified backup
is the same size. IV/Greeks are present for 2444 sessions
(2017-01-03 through 2026-09-23). The remaining
252 price-only sessions cover 2016-01-04
through 2016-12-30; Greeks remain explicitly missing and retain
the documented historical reference proxy. A completed date does not imply
every contract/field has complete provider coverage or verified deliverables.

Cumulative requests: 5659; received bytes: 19406520046;
automatic retries: zero; duplicate durable attempt IDs: zero. No staged source
JSON-gzip files or pending-cleanup journal remain. The process has exited.
No recurring collector was activated. This completed population must not be
refetched without a new explicit finite request.

Final evidence: .local/theta-spy-20260924/completion-verification.json;
database: data/options.sqlite; backup:
.local/theta-spy-20260924/completed-options-backup.sqlite.
