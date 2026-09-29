# Sharadar direct migration — September 12, 2026

Status: Implemented, independently verified, and applied to the canonical company store on September 12, 2026. No provider acquisition was performed during implementation or application.

The user explicitly requested implementation after the completed channel comparison.
The existing Nasdaq-delivered backfill remains authoritative history. Direct delivery
becomes the default for future Sharadar refreshes. The migration does not complete
the held ARQ OUST–PBF historical partition, resolve the three mapping gaps, repeat
the backfill, or change recurring execution.

## Delivery and canonical identity

The fixed host uses `api.sharadar.com/v1.0/data/fundamentals` and
`/v1.0/data/descriptions`. The existing named credential reader loads
`SHARADAR_DIRECT_API` and sends it only as `x-api-key`. The host binds a key to its
delivery route before making an HTTP request. Legacy Nasdaq adapters remain for
retained evidence and explicit historical fixtures; the default host cannot send
the direct key to Nasdaq.

The checked-in typed contract maps all 112 fields using the retained Nasdaq field
types and the captured direct SQLite schema. Native `date` maps to canonical
`datekey`; dimensions and source-key components remain unchanged. This is the
explicit bridge between two delivery channels of the same Sharadar product.
Direct observations reuse the established key contract and observation IDs, while
schemas, normalization, acquisition IDs, capture scopes and original bytes identify
the direct channel. Native metadata is the complete descriptions response; the
adapter's temporary array representation is never retained as original evidence.

A direct row with the same canonical values, missingness, types and key reuses the
existing version only for the reviewed `nasdaq_sf1.v1` / `sharadar_direct.v1` pair
with matching canonical schema contract. Its direct capture membership is retained.
Changed values or `lastupdated` append to the existing predecessor chain. Ordinary
metadata changes within one channel keep the existing versioning rule. Readers
retain canonical `source_datekey`, disclose each version's `delivery_channel`,
`source_table` and `normalization_version`, and keep local capture time as availability.
An old-period correction is therefore visible only at its later capture cutoff.

Direct descriptions normalize `table=fundamentals` to the established logical
`table=SF1` and indicator `date` to `datekey`. They keep native raw bytes and
`/data/N` pointers, and extend the existing definition history. Direct TICKERS
identity preparation accepts native object rows and string permanent identifiers.
The selected refresh code reuses its pinned mappings; no new identity pull
is added to that operation.

## Completeness, state and bounds

Direct object pages require exact `count == len(data)`, the reviewed 112-field
inventory, an explicit single dimension and bounded canonical ticker set. Query
parameters include JSON format, ascending date, explicit date bounds, limit and
offset. A full page requires another offset request; only a short final page
establishes transport completeness. Duplicate source keys, offset discontinuities,
changed scope/schema and date-order regressions fail before publication. Remote
snapshot coherence remains unproven.

The selected refresh retains its existing invocation ceiling of 200 requests,
256 MiB, 3,600 seconds and 1,000 requests per day, with at least one second between
requests and no hidden retry. Each fundamentals walk is capped at 100 pages,
100,000 rows and 256 MiB. Descriptions use one request, at most 1,000 rows,
16 MiB and 30 seconds; a full descriptions page is not considered complete.

One descriptions response supplies both definitions and SF1 metadata. The direct
refresh uses a separate `direct` state directory and version-2 state contract.
Existing baseline proof is reused. Incremental acquisition preserves the seven-day
`lastupdated` overlap and broad date window so a correction to an old period can
be captured. A pending window's bounds remain fixed across restarts. Missing
baseline scopes block before any provider request; they never trigger a full pull.

## Database and registry

Company migration `0017_sharadar_direct` follows 0016. It rebuilds only the schema
version table and the two membership tables, copying every existing column value.
It adds the direct normalization value and native pointer form, restores all 19
existing dependent triggers, and adds two guards tying pointer form to acquisition
channel. Prior migration resources are immutable. The existing migration runner
applies the resource transactionally with foreign-key validation.

Registry 2.82 adds the migration and two bounded direct collector declarations with
reciprocal dataset bindings. The predecessor projection reproduces exact registry
2.81; generated tool catalogs and other datasets are unchanged. Declarations are
not timer activation or evidence that the canonical migration has run.

## Validation and application evidence

The reconciled full offline gate covers **2,316 unique passing cases**, with no
uncovered cases, unresolved failures, or skipped cases. It combines retained and
resumed runs, rather than one uninterrupted process. The resumed 366-case run
passed in 4,758.218 seconds. Focused direct/real-publisher/migration acceptance
also passed all 21 cases in 21.516 seconds.

The interrupted run exposed a missed Inspector current-registry pin, corrected
from 2.81 to 2.82 without relaxing its exact-version/schema guard. All affected
Inspector tests were rechecked. Separate transcript changes made during the long
run were preserved and their complete consumer group was rechecked against a
new frozen snapshot: 146/147 initially passed; the only error was two schema
fixtures omitted from the validation copy. The exact workspace files were added,
and all 11 schema-contract cases passed, including the failed case. These add
18 unique cases to the original 2,298-case inventory. Original failures and
explicit successful rechecks remain in the receipts; duplicates count once.

A fresh independent reviewer verified the case union, all 921 final snapshot
files against the workspace, all 53 task-code hashes, all 56 migration-resource
checksums, and the application helper. There were no outstanding findings.
The initial concurrent-change inventory used modification times; all known
changed transcript consumers were subsequently retested against hashed copies.
No browser or new live-provider validation is claimed.

The single company application began at **15:47:18 UTC** and its verification
completed at **15:55:27 UTC**. The helper exited successfully with
`outcome=applied_verified`. It held one physical company-store lock across the
immutable before/after snapshots and the existing transactional migration seam.
The runner's foreign-key validation passed before commit.

All **15 Sharadar tables / 1,459,244 rows** have identical before/after typed-row
hashes and counts. This includes all **360,359 observations, versions, current
heads, and SF1 memberships**, plus all 112 definition versions and memberships.
Every prior company migration row (16) and existing trigger body (276) is
unchanged. Exactly migration `company:0017_sharadar_direct` and the two reviewed
origin guards were added. An independent reviewer also compared the saved
before/after receipts and verified both new trigger hashes against the reviewed
SQL; that review did not reopen the database. No provider request, backfill request, or scheduler
change occurred. The recorded ARQ OUST–PBF baseline gap remains; its existing
refresh guard was retained.

An attempted increase of the descriptions timeout from 30 to 600 seconds was
rejected by automatic approval review as expanding approved provider duration.
The final code retains the 30-second cap, uses a 20-second HTTP timeout, and
passes the original deadline through publication. No expanded-duration request
was executed.

Private evidence is retained under `.local/sharadar-direct-implementation-20260912/`:
`final-code.patch`, `final-code-baseline.json`, `final-validation-result.json`,
`independent-verification.json`, the retained validation logs and per-case events,
`canonical-before.json`, `canonical-attempt.json`, `canonical-after.json`, and
`canonical-result.json`, and `canonical-independent-verification.json`. The earlier
finite channel-comparison evidence remains
separate and was not repeated.


#### September 12 operational attempt and remaining cap decision

The gap recovery completed at 17:22:15 UTC in 18 seconds: two GETs,
3,237,483 received bytes, one complete direct ARQ capture and 1,497 new
observations. The 17:23:14 UTC audit confirmed all required baseline scopes,
361,856 observations/versions/heads, unchanged migration ledger, zero
Sharadar foreign-key errors and no missing heads. All 533 prior captures and
the original failed Nasdaq attempt remain retained.

The one incremental invocation completed at 17:23:37 UTC with
page_http_failure after two GETs and 42,963 bytes. Descriptions succeeded;
the first fundamentals request returned HTTP 400 with the provider's explicit
200-character ticker-parameter limit. No fundamentals partition or refresh
watermark advanced. No automatic retry or scheduler action occurred.
The subsequent structural store audit passed; it does not establish refresh
completion or permission to activate the timer.

The direct request builder now packs batches within 200 characters as well
as the existing 100-ticker ceiling. The exact selected manifest requires
300 partitions, reusing the successful descriptions response and fixed
2026-09-02 through 2026-09-12 update window. Thirty-seven focused offline
tests passed, plus five isolated activation-gate checks. The gate requires
a successful process/result, no pending checkpoint, matching complete date
and window, and a later passing store audit.

The existing 200-request per-run ceiling remains unchanged. The unapplied
proposal requests a 400-GET manual continuation and future daily cap, with
256 MiB, 3,600 seconds, 1,000 requests/day and no automatic retry unchanged.
This additional request authority is pending an explicit user decision.
Both Sharadar units remain uninstalled/inactive; the first successful full
refresh and its audit still gate their already-authorized activation.

Exact manifests, original failed response, preserved attempt charges, tests,
audits and the unapplied cap patch are under
.local/sharadar-direct-activation-20260912/.


#### September 12: 400-request continuation stopped; 30-ticker boundary

After the explicit 400-request approval, 24 focused checks passed. The single
new continuation at 19:46:45 UTC made one GET, received 173 bytes and stopped
with HTTP 400: the provider also accepts at most 30 tickers per request; the
first character-limited batch contained 44. It published no new fundamentals
and left all 450 corrected partitions pending. Cumulative live requests in this
operational completion are five: two successful gap GETs, the initial two-GET
refresh attempt, and this one failed continuation GET. No automatic retry or
scheduler operation occurred.

The builder and parser now enforce both 30 tickers and 200 characters. The fixed
selected update manifest therefore contains 450 partitions, 75 per dimension,
with maximum 143 ticker characters. Its existing successful metadata is reused.
The pending-checkpoint bound now accommodates all 450 partitions; an isolated
restart test verifies that a fully acquired 450-partition checkpoint finalizes
without another GET. The fixed selection, dates, byte/time ceilings, stored
history and all previous attempt charges are unchanged.

Production remains at the approved 400-request cap. The separate unapplied
500-request proposal covers one corrected continuation and future daily runs
(the full daily manifest requires 450 fundamentals GETs plus metadata).
It retains 256 MiB, 3,600 seconds, 1,000 requests/day and no automatic retry.
New request authority is pending; the timer remains uninstalled/inactive.
Private evidence: continuation-1/,30-ticker-refresh-manifest.json,
30-ticker-final-tests.log,500-request-proposal.json and
proposed-500-request-cap.patch under
.local/sharadar-direct-activation-20260912/.

The final correction passed 40 unique focused tests (39-case check plus a
27-case affected recheck including the added checkpoint case). Independent
read-only review reconciled the tests and all five source hashes, verified
the exact 450-partition manifest, and found no remaining code blocker.
Operational continuation and timer activation remain incomplete.


### Completed direct daily activation — September 12, 2026

The 500-request continuation succeeded with all 450 selected partitions,
450 GETs and no retries. The immutable audit found 1,321 new observations,
236 revised versions, and 1,940 unchanged-version reuses. Canonical history
now contains 363,177 observations and 363,413 versions; migration history and
retained source evidence are preserved, with no foreign-key, lineage or
duplicate-version errors. The earlier 1,497-observation ARQ gap is closed.
PFBC, PSQL and TOWN remain the existing identity gaps.

The daily timer was enabled and observed active/waiting at 23:07:52 UTC;
its first scheduled run is September 13 at 05:00 EDT. It uses Sharadar direct
with at most 30 tickers and 200 ticker characters per request, at most
500 requests, 256 MiB and one hour per run, and no automatic retry. The
activation did not start the recurring service. The latest cap recheck passed
27 tests; independent verification certified the saved refresh/audit/activation
evidence. Future scheduled execution remains unobserved.

The [operating envelope](CURRENT_OPERATING_ENVELOPE.md#september-12-sharadar-direct-refresh-complete-and-daily-timer-activated)
records the final authority and activation; exact private receipts are under
.local/sharadar-direct-activation-20260912/continuation-2/.


## September 17, 2026 UTC — bounded daily-refresh clock recovery

The user approved making the Sharadar daily collector tolerate brief host-clock
regressions after the September 16 05:00 Eastern run stopped. Its 59 requests
all returned HTTP 200; 58 of 450 partitions had publication receipts. The last
request was charged at 09:01:06.803923 UTC, its response was captured at
09:01:06.136078 UTC, and the following queue invocation hit the backward-clock
guard. The September 16 checkpoint remains pending; this implementation does
not constitute completion or a manual retry.

The daily runner now uses a Sharadar-only policy around the existing
BoundedForwardClock. Each clock recovery waits at most five monotonic seconds,
further limited by the original invocation or active operation deadline.
Definitions retain their 30-second ceiling. A clock that has not recovered
within that allowance stops with clock_recovery_exhausted; credential-free
structured journal events distinguish waiting, recovered and exhausted.

UTC is always an actual host reading. Original response bytes and timestamps
are unchanged. The clock remembers request and observed response times, and
seeds a resumed run from the durable last request and its matching retained
response. The transport returns its response for retention before any recovery
wait can fail. Existing cutoff validation, publishers, pending-window bounds,
uncertain-request protection and no-retry rules remain authoritative.

One invocation-wide monotonic pacing record enforces the existing minimum
request interval across definitions, fundamentals partitions and pages.
Pacing happens before queue reservation; insufficient remaining request time
defers without a new charge. Across process restarts, a conservative initial
interval supplements the queue's existing durable UTC spacing check.
Optional pacing and absolute-deadline arguments in the direct controllers
leave callers that omit them with their existing behavior. The shared queue
and clock helper are unchanged.

Validation uses fake clocks, forbidden live transport, temporary queues and
real publishers against temporary stores. Coverage includes small and persistent
regressions, forward clock steps, exact response preservation, retained-only
recovery, original deadlines, page pacing, fixed-window continuation, HTTP
no-retry behavior and same-day zero-write replay. The focused run passed 36 tests; the final
14-test clock module, including two additional controller checks, also passed.
This covers 38 distinct passing cases, with no failures or skips. Validation
receipts are under .local/sharadar-clock-recovery-20260917/. No provider request, operational-store
open, unit/timer change, commit or push was performed for this implementation.

## September 22, 2026 — bounded Sharadar server-error recovery

The user approved implementation and one catch-up of the failed September 22
05:00 EDT refresh, with recovery attempts 10 and 30 minutes after the first
server failure, then the next scheduled day. This supersedes the former
no-retry rule only for known HTTP 5xx responses in the selected direct Sharadar
refresh. Authentication failures, HTTP 429, malformed evidence and uncertain
transport attempts remain stopped.

The Sharadar-only controller preserves the failed response and its bytes,
records an immutable link to each new acquisition attempt, keeps request
parameters and the pending date window fixed, and skips completed partitions.
The existing shared queue still reserves and charges every request before GET.
Its default behavior and all other providers are unchanged.

There are at most two timed recovery slots per Eastern calendar day across
the entire refresh, persisted across process restarts. The slots occur at
first failure +10 and +30 minutes. If the first retry is late, the second
waits at least another 20 minutes. A server Retry-After can postpone a retry.
On a subsequent scheduled day, the retained failed request gets one new initial
attempt, then the same two timed slots if needed. The invocation never rolls
into another day's allowance.

All attempts and waits fit within the existing 500-request, 256 MiB, one-hour
invocation limits, one-second pacing, 30-second request timeout, and
1,000-request UTC daily ceiling. A reached limit defers unfinished work.
No timer cadence, unit restart policy, credentials, schema or data semantics
change. Existing successes and historical failure receipts are not rewritten.
The service loads these source changes on its next invocation.

Saved Sharadar summaries now include the status code, dimension, symbol count,
checkpoint progress and recovery disposition. These are closed, validated
fields; raw provider errors and credentials are excluded. Historical run
receipts retain their original exit result.

Validation uses temporary stores, fake time and no provider access:
tests.operations.test_sharadar_recovery, test_sharadar_run_details, the existing
Sharadar selected/direct/clock regressions, saved-run history and Inspector
fetch-status checks. Independent verification precedes the approved live
catch-up. Actual activation and catch-up evidence are recorded separately.


### September 22 implementation validation and catch-up completion

The integrated component checks passed: 90 tests in 76.473 seconds, with no
failures or skips. Independent review closed two findings before activation:
publication receipts now identify the successful retry evidence, and a scheduling
pause cannot reset the original supervisor deadline. The final four-case details
and daily-limit recheck passed; all 11 reviewed source/test fingerprints were
stable across it. Offline checks used temporary stores and blocked network.
The full suite was not required for this component-scoped change.

Desktop and mobile Chromium fixture checks confirmed visible recovery details,
working expand/collapse, and no horizontal overflow. This is fixture browser
evidence, not a claim about a live Inspector deployment. The unrelated derived
status edits were preserved; their writer's stopped state was not independently
established, but the reviewed files remained stable. The fixture server was
stopped after the checks.

The one authorized catch-up ran on September 22 from 23:12:47 to 23:21:02 EDT,
systemd invocation 3d304b27b250441f8429f4441b15ffaf, exit 0. Its saved run is
4f26afa4ce6f4550b8edd5423660b9b9. The previously failed MRQ request returned
HTTP 200 on the first recovery attempt. Because the original failure occurred
that morning, the first retry was already due. No second retry was needed.

The run completed the remaining 447 partitions: all 450 are complete for the
fixed September 22 window (lastupdated September 14 through September 22).
There were exactly 447 additional requests, all HTTP 200, 1,529,390 response
bytes, and 494.709 seconds of recorded run time. No further manual invocation
was made. The existing daily timer remains active for September 23 at 05:00 EDT.

The approved immutable postcheck verified all 447 new response hashes and
publication links, 719 capture memberships, preservation of all prior Sharadar
identities and five full prior-version samples, and zero missing heads.
It found 96 new observations, 389 new versions and 447 new captures. The
original HTTP 500 evidence and morning failure receipt are unchanged; the
successful catch-up has its own receipt. A task-helper identity assertion was
corrected to use the publisher's existing snapshot-to-capture mapping; this
required no production or data change. This was a scoped check, not a full-store
audit.

Evidence: .local/sharadar-retry-20260922/, especially final-review-pins.json,
integrated-tests.log, review-approved.json, launch-intent.json,
launch-receipt.json, postcheck.json and completion.json. All reviewed source
fingerprints still matched after the live run. No commit or push was performed.
