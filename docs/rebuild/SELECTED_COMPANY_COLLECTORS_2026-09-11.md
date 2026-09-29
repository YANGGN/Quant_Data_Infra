# Selected company collectors — September 11, 2026 UTC

Status as of 2026-09-11T21:44:50Z: all six collectors are implemented for 2,201 issuer-ready symbols. The corrected backfill is running under PID 286119; 128 new requests have completed since restart, with 23,207 queued. Fourteen previously rejected responses were recovered without GETs. Three source rejections, one held uncertain CHEF request, and 47 identity gaps remain explicit. Weekday activation remains pending the original full-workload audit.

The user explicitly requested expanding earnings, FMP statements including full
as-reported statements, analyst estimates, recommendations/ratings, price targets,
and product-revenue segments to the selected universe, one database backfill,
and live-fetcher integration. The user subsequently selected **all six groups
every weekday**. This supersedes their earlier prepared-only endpoint disposition.
Dividends, splits, prices, SEC, Sharadar, news, transcripts, options and macro are
outside this task's collection expansion.

## Population and finite backfill

The retained membership has 2,248 securities. Current FMP company prerequisites
permit 2,201 symbols. There are 45 missing canonical issuer records and two
missing CIKs. They remain explicit gaps; this operation does not create issuers
from unaudited associations or change company identity semantics.

The frozen initial manifest contains 37,173 GETs and excludes 4,646 previously
populated endpoint/period scopes, including 14 evidenced empty responses. It
requests available annual and quarterly statements/segments, historical and
upcoming earnings, annual/quarter estimate pages, dated recommendations and
price targets, and separate current distributions/consensus/summary responses.
A 19-unit pilot checks every endpoint/period before broad acquisition. Admission
may span multiple batches; broad acquisition and pagination wait for all pilot
responses to pass.

The exact aggregate ceiling is 67,539 requests (including at most ten pages per
new estimate period), 8 GiB, and 72 hours from the worker's original monotonic
start. There are zero automatic retries. Endpoint limits remain those of the
existing selected adapters. Short pages and completed requests do not prove
unlimited source history; earlier forecast periods are not old estimate vintages.
The original statements and segments retained for AAPL/MSFT are preserved rather
than silently repeating their completed request scopes.

The worker uses the existing FMP_API_KEY resolver, shared account allowance,
HTTP-only process pool (at most 24), exact response retention, source parsers,
physical-store locks and replay-safe publishers. One parent publishes to
`data/company.sqlite`; network acquisition finishes before canonical publication.
Immutable source bodies are also retained at their canonical source references
under `data/.operations/collection/fmp/blobs`. Invalid source responses have
explicit private failure records. Uncertain provider requests stop acquisition
and preserve charges; no timeout is treated as an empty result.

## Weekday refresh and allowance

The prepared fixed timer is Monday–Friday **20:00 America/New_York**, with no
catch-up, randomized delay or automatic restart. Each cycle requests all 19
endpoint/period combinations for every issuer-ready selected symbol: currently
41,819 initial GETs. Live estimates request the current bounded first page for
each annual/quarterly period; initial backfill separately walks older pages.
Weekday statement requests revisit the latest five annual and twenty quarterly
records per endpoint/company, covering recent reports and restatements without
repeating the full available statement history daily. Other endpoint windows
retain their established bounds. The measured pilot returned 2.82 MB across
19 scopes, with 1.93 MB in the two full as-reported histories, motivating this
bounded current statement window. The cycle limits are 50,000 GETs, 4 GiB and
12 hours, with a 12h5min host timeout.
No other existing timer is repurposed or manually executed.

The explicit daily expansion requires increasing the account's **local** daily
ceiling from 30,000 to 60,000. Shared 120 ms spacing, 6,000 maintenance reserve,
effective date, prior charges and all other finite operation caps remain intact.
This is a local control under the already recorded FMP Premium plan, not a claim
that the provider offers a 60,000/day plan limit or a new subscription purchase.
The exact config/allowance transition uses the existing account lock and preserves
before/after receipts; activation is a separate audited operation.

Live activation requires the matching membership/provider mapping and a verified
backfill audit. The new schedule and numeric run outcomes use the existing Live
Fetcher rendering. Unit files or active collection bindings alone do not prove
that the timer is installed or that scheduled collection has executed.

## Validation and evidence

The scoped change reuses existing identity, time, missingness, schema, physical
locking, shared pacing and canonical publication semantics. Applicable focused
and adjacent checks plus independent operational/scheduler review are required;
no new full-suite or migration gate is claimed for this bounded adapter change.

Initial focused checks: 29 passed. Adjacent checking exposed an existing transport
fixture's access to the live allowance and a new entrypoint constant assumption.
The transport stopped before dispatch but reserved one local unit; its original
charge and incident evidence are preserved for exact reconciliation. Both
transport fixture classes now isolate the host allowance. The entrypoint uses
the literal timer identifier expected by the established CLI check. Original
failed results are retained alongside correction rechecks.

Preparation, source preimages, finite manifest, allowance proposal and test logs:
`.local/company-collectors-expansion-20260911/`.
Operational receipts after actual launch:
`data/.operations/collection/company-selected/`.

The corrected adjacent selection passed 53 tests in 26.473 seconds. The initial
29 focused tests passed in 10.112 seconds. Independent review accepted the exact
proven-unsent allowance repair; its execution preserved usage 1,125 for September
11, the original attempt, policy and last-request time, clearing only stopped.
No provider callback or canonical write occurred during that repair.

Independent review found and corrected pilot admission across batch boundaries
and uncertain-attempt status accounting. All 12 selected-company tests passed
in 11.666 seconds after those corrections. Attempted unresolved work is partial,
never reported as unattempted or refunded. The initial run exposed concurrent company writes during immutable identity
reads. The correction coordinates complete pure reads with the existing ordered
market/company physical locks and releases them before network or publication.
An actual identity or sidecar change still fails closed; the adapter does not
retry a publisher or change the canonical reader contract.


## Actual launch and retained-source corrections

The binding/allowance cutover succeeded at 02:53 UTC. A first configuration
write was rejected before any config or allowance change because a private
receipt helper required mode 0600; the independently reviewed correction
preserved repository configuration mode 0644. Both preparation receipts remain.

The worker began at 02:54 UTC. Its first 19 GETs returned 17 accepted responses
and two as-reported responses containing spelled-out English document dates.
The parser now accepts that unambiguous date-only source encoding, preserving
raw payloads, precedence and conflict checks. Both original responses then
published 84 rows with zero additional GETs. A second bounded acquisition of
19 never-attempted scopes exposed a nonbreaking-space spelling of the same
format. That source-local spacing correction and a settled-checkpoint
continuation preserve the pending AAL quarterly estimate page and all charges.

The named-month parser/adjacent selection passed 34 tests in 40.919 seconds;
the tighter original-deadline check passed separately. The subsequent spacing
and settled-continuation selection passed 15 tests in 15.346 seconds. Original
failures, exact bodies, test logs and independent review baselines are retained.
Broad-population completion and recurring activation are not yet claimed.


## Coordinated identity reads and recovery

The first broad run received 1,856 more responses, then stopped during a pure
identity read. All HTTP requests were settled, 1,814 outcomes were recorded,
and 42 original responses remained unpublished. Explicit recovery published
21 with zero GETs before a different immutable-reader exit guard detected a
concurrent writer. The recorded traceback places that failure before the next
canonical publication. All original receipts, markers and failure logs remain.

Complete identity/preflight reads now use the existing ordered physical locks
for market and company. The read connection remains immutable; locks and handles
close before provider requests or canonical publishers. Acquisition and read
cleanup remain inside the original invocation deadline. The adapter retries
only a pure read for the exact existing not-quiet error; identity and sidecar
changes remain errors. It never retries publication or HTTP.

The next fixed recovery publishes only the remaining 21 originals, skips the
43 already-published last-batch responses, and reconstructs pagination for all
64, including 16 child pages not present in the last completed checkpoint.
The settled continuation retains 1,875 charged requests and 25,783,420 bytes;
the outer original pilot adds 19 GETs. It retains 35,319 pending units, the
original absolute deadline, request/byte ceilings, and zero HTTP retries.

The coordinated-read and adjacent deadline selection passed 49 tests in
54.854 seconds. The final audit requires hashes for both private batch responses
and successfully referenced canonical source blobs, matching canonical evidence,
zero foreign-key violations and no unresolved request or pending unit. Timer
activation remains conditional on that operational audit.


## Durable completion sequence and Live Fetcher

Independent review accepted the coordinated read/recovery correction, then the
corrected final audit and activation drivers. Three fresh fixture regressions
passed in 2.710 seconds. All 42 retained responses were recovered with zero
additional GETs by 04:01:15 UTC. Broad acquisition resumed at the original budget
and deadline, without refetching any completed request.

A separately reviewed, single-use completion worker started at 04:09:40 UTC.
It performs no provider requests. It checks the exact backfill process arguments,
source fingerprints, original boot/deadline and settled result, then invokes the
canonical audit once, activation preflight once, and timer activation once.
Failure, pending work, uncertain requests, missing/corrupt evidence, changed
reviewed sources or expired deadlines prevent activation. No recurring Codex
monitor or new user task was created.

The local Inspector was reloaded on its existing loopback port 8766. `/healthz`
and `/status` returned 200, and the actual HTML contains `Selected company inputs`.
Browser automation could not initialize because of the desktop sandbox runtime;
no browser visual-check claim is made. The existing rendering and schedule tests
passed. The first health probe used the wrong route `/health`; it was corrected
to `/healthz` without repeating the server restart.

The current observation is in `.local/company-collectors-expansion-20260911/rollout-status.json`.
Completion/failure receipts are `finite-finalizer-result.json` or
`finite-finalizer-failure.json` in that directory. Canonical audit and actual
timer activation evidence remain under `data/.operations/collection/company-selected/`.
Backfill completion and timer activation were still pending at this observation.

### 2026-09-11 14:05 UTC checkpoint and performance follow-up

Continuation 3 stopped at 13:15:37 UTC before the next provider batch. The
activated binding fingerprint changed because the separate news binding moved
from prepared to active; all four bindings for these six company dataset groups,
the FMP allowance and the selected membership/mapping remained unchanged. The
single-use completion worker consequently stopped without audit or activation.
Their failure receipts remain preserved.

Continuation 3 completed 5,802 additional requests, for 7,677 charged requests in
its checkpoint (7,696 including the first outer invocation), 559,101,617 bytes
before the outer invocation and 29,939 pending units. All attempted response
identities have corresponding settled outcomes. No provider request is running
for this workload at this observation.

A new private continuation-4 manifest and input explicitly rebase only the
configuration/scope fingerprints. The original cutoff, memberships, provider
mappings, subjects, every request/unit identity, completed outcomes, byte/request
charges, pagination state and original monotonic deadline 311489.150269794
remain unchanged. The current host binding is validated normally. The original
manifests and receipts are unmodified. The completed continuation preflight
made zero GETs and is recorded in continuation-4-check-result.json.

The canonical research-row predecessor and issuer queries were confirmed to
scan the whole research table. A candidate additive migration,
company:0016_fmp_research_lookup_indexes, adds only two nonunique lookup indexes.
The candidate is isolated under
.local/company-collectors-expansion-20260911/performance-candidate-20260911.
The required full offline suite is running there; source/fixture review and
focused tests passed. The guarded application adapter additionally passed seven
temporary-root checks for commit/rollback classification, forward file repair,
preserved recovery stages, wrong-lock rejection and timer-margin expiry.
Neither this migration nor registry 2.81 has been integrated or applied yet.

The index application must wait for the independent Equibles finite worker to
finish normally and for a quiet interval before the ordinary company timers.
No other worker or recurring unit is stopped, restarted or triggered. After
the full-suite and independent gates pass, the original finite company workload
can resume from continuation 4. Its updated one-use audit/activation helpers
retain the original deadline and request limits; weekday activation remains
pending actual completion and the canonical audit.


Validation continuation recorded at 2026-09-11T16:19:12.477554+00:00. The original full offline run is
still executing against its frozen 884-file candidate. An independent review
identified 21 stale latest-registry migration-count assertions in 20 test files.
A separate successor candidate changes only those literal expectations from
54 to 55; all production bytes and the 38 integration preimages are unchanged.
The original candidate, run log and failures are preserved.

The accepted resumed-run lane in TEST_STRATEGY section 4 requires the original
native run to finish, all 42 cases in the affected modules to pass serial
rechecks, exact reconciliation of 2,209 occurrences / 2,205 unique cases, and
independent review of the resulting evidence. The revised private recorder and
application adapters explicitly retain the failed native exit separately from
the successful rechecks; no clean native full-suite pass is inferred.

The v4 continuation and finite completion source proofs passed independent
review; seven mocked supervisor checks and the current zero-GET continuation
preflight passed. Actual validation acceptance, index application, provider
continuation and weekday activation remain pending. The independent Equibles
worker has exited, and the scheduled company services were observed not running.
Their state and timer margin will be rechecked before any index application.


### 2026-09-11 validation correction update

The frozen original run currently reports 24 failures and 29 errors. Its final
native exit has not yet arrived. The 21 migration-count assertions described
above are joined by three host-binding fixture failures, two missing ticker-file
errors, and 27 Inspector constructor errors caused by its exact registry-version
guard still expecting 2.80.0. The successor candidate now includes the existing
main-tree host-fixture correction, the exact approved ticker CSV bytes, and the
production Inspector guard correction to 2.81.0. Existing main-tree fixture and
CSV files are pinned prerequisites and will not be overwritten.

The current frozen successor is performance-candidate-v4-20260911 with manifest
SHA-256 a440821ed7f55c268541d4127cead25dd8ac06e9c0a4d7d57f0f0296097b59a6.
It contains 885 pinned sources and 39 integration writes. The affected recheck
set is now 90 cases across 24 modules. Successful reconciliation must preserve
the original native failure, account for every original case and require all
90 corrective cases to pass. The Inspector change is a production compatibility
correction, rather than only test metadata.

Nine mocked completion-sequence checks passed in 0.050 seconds, including
rejection of the new Inspector source before index integration and rejection
of the old source afterward. The application source preflight passed with zero
canonical access and zero provider requests. These results do not constitute
the still-pending full-run/recheck acceptance, migration application, backfill
completion or weekday activation. The original GET/byte caps and deadline are
unchanged.


The subsequent independent isolation review accepted running the 24 corrective
modules serially in one separate successor process while the original full run
continues. It verified unique temporary stores/publication paths, physical lock
isolation, ephemeral or mocked HTTP ports, and mocked/injected host readers.
The recheck began with TMPDIR=/tmp and bytecode disabled, using its own log and
native-exit receipt. This supersedes the earlier proposed wait-before-recheck
sequence; actual migration acceptance still requires both complete native
results and exact evidence reconciliation. See performance-recheck-isolation-review.json
in the private rollout directory for the review evidence.


The 90 corrective cases completed in 793.459 seconds with native exit 0,
no failures/errors/skips and an exact match to the 24-module case inventory.
Their log SHA-256 is ee0c84fe03f9ca561aac06e92f78d4ed7e6f656ff7c1ff525468bd5298f604dc;
the native-exit receipt SHA-256 is 109ce843774ee9fba7cd3ffb7e905dcfa792721001e6899de8cf8c03d515b501.
The original full run is still executing, so complete evidence reconciliation
and operational acceptance remain pending.


### Completed validation and first index rollback  September 11, 18:1018:37 UTC

The original native full run completed with exit 1: 2,209 executions in
16,585.598 seconds, 24 failures and 29 errors. All 53 findings are explicitly
attributed to the retained correction set. The successor recheck passed all
90 cases across 24 affected modules with exit 0. The final reconciliation covers
2,205 unique cases, no unresolved or uncovered cases, and no skipped cases.
Independent review accepted the actual completed evidence. This is
passed_after_explicit_rechecks, not a clean original native full-suite pass.
The frozen successor migration and 885 source hashes remain unchanged.

At 18:10 UTC the one-use index application reached the migration runner's
mandatory whole-company foreign-key check and exceeded its 20-second SQL bound.
The transaction rolled back. Its before/after capture is identical: company
ledger remains at 15, both proposed indexes are absent, 232,472 FMP research rows
and 4,334 snapshots are preserved, and all 276 trigger definitions match.
All 39 source stages and original failed receipts are retained; source
integration, Inspector reload, provider continuation and weekday activation
did not occur.

Two bounded immutable read-only probes did not finish the whole-company check.
The second recorded underlying OperationalError: interrupted at 90 seconds.
Neither probe establishes a completed whole-company integrity pass.

A separate one-use recovery adapter reuses the exact verified staged bytes,
retains the same migration transaction and mandatory full foreign-key check,
and allows 3,600 seconds for SQL, 30 seconds for state reconciliation and a
60-second publication reserve. Its separate supervisor allows 4,020 seconds;
timer separation is checked again after acquiring the physical company lock.
A 64 MiB connection-local SQLite cache does not change persistent database
settings. The original boot, 72-hour deadline, GET/byte caps and zero-provider-
retry policy remain unchanged. Ten temporary-root migration recovery checks
passed in 4.063 seconds; ten mocked supervisor checks passed in 0.052 seconds.
Execution awaits independent acceptance of queued-recovery-preflight.json.


At 18:37:54 UTC, the separately reviewed recovery supervisor (PID 266290)
started application PID 266327. Independent reruns passed all 20 focused
checks. The recovery has reached its one-use started marker; migration
completion, provider continuation and timer activation are still pending.


### Index committed and existing backfill resumed — September 11, 20:26 UTC

The index recovery committed at 18:43:23 UTC after 326.887 seconds, including
the mandatory whole-company foreign-key check. The ledger advanced to 16;
both new lookup indexes are present and the expected queries use them.
All 232,472 pre-existing research rows, 4,334 snapshots and trigger definitions
were preserved. All 39 integrated source files match their accepted hashes.

The supervisor subsequently stopped on a two-second dashboard timeout.
The existing Inspector process remained running. A single later check returned
HTTP 200 for both /healthz and /status; /status took 13.934 seconds and contained
Selected company inputs. No further restart or dashboard code change was needed.

At 20:26:01 UTC the already reviewed continuation-4 worker (PID 268782) and
finite audit/activation finalizer (PID 268783) were launched directly, preserving
the original boot, deadline, request/byte caps and all completed requests.
The first batch published 64 responses successfully. The earlier failed
supervisor receipt is preserved; the migration was not repeated.
Weekday activation remains conditional on the completed canonical audit.


### Share-class capture ordering correction — September 11, 20:40 UTC

A cash-flow response for BATRK arrived 0.288822 seconds before BATRA's response,
but batch publication followed request order. Both share classes map to the same
issuer and the canonical publisher correctly refused the older revision.
The collector now publishes each batch by original capture time, reading only
receipt metadata for ordering. Its existing timestamp and identity contracts
are unchanged. That exact stale-revision error becomes an explicit publication
rejection; unrelated conflicts still stop the invocation. Rejected raw evidence
is retained, and canonical timestamps or newer rows are not rewritten.

All 27 focused collector checks passed in 23.274 seconds, including reversed
response order, preservation of original capture time, real stale-publication
rollback and propagation of unrelated conflicts. The first test run's single
fixture timestamp-format failure and the passing correction are both retained.

The interrupted sixth batch had 64 settled HTTP responses and no uncertain
requests. Its 21 completed publications were preserved. The other 43 responses
were reconciled without provider requests: 42 published successfully and the
already failed BATRK annual cash-flow response remains an explicit rejection.
That failed publication was not repeated. Across continuation 4, all 384 requests
are accounted for; the next input preserves 8,061 requests (8,080 including the
outer pilot), 588,530,124 bytes before the outer pilot and 29,555 pending units.

The existing continuation and audit/activation helpers were reused with this
new checkpoint. Continuation 5 (PID 271114) and finalizer v3 (PID 271115) launched
at 20:40:29 UTC. The original caps/deadline, weekday unit, cadence and activation
audit are unchanged. No additional migration or full-suite rerun was needed for
this bounded collector fix.

## September 11 — uncertain CHEF request and untouched-work continuation

The user's renewed request to finish the six collectors and start backfill
covers continuation of the original finite population. At 21:16 UTC,
continuation 5 stopped after 14,281 charged requests, 14,280 settled outcomes,
and 1,116,255,501 retained response bytes. The exact uncertain request is CHEF,
annual financial-statement-full-as-reported, shared attempt
2026-09-11-18878. Its transport recorded StoreUnavailableError and retained
no response; this does not prove that no HTTP request was sent.

The one-use continue_untouched_6.py preserves that original attempt and
classifies it as held/uncertain, without retry, refund, a fabricated response,
or a canonical publication. The narrowed manifest removes only that request,
subtracts one call and its maximum 8,388,608 response bytes from available
ceilings, and keeps the original boot and absolute 72-hour deadline. All 14,280
settled outcomes and estimate page predecessors remain in the continuation.
Its 23,335 queued units exclude the uncertain request. Aggregate reporting adds
the held charge and the original 19-call outer pilot; the 67,539-call and 8 GiB
original limits remain upper bounds.

The exact shared account transition is prepared under the existing account
lock. It must match the recorded before-image, retain all daily charges
(including the later already-in-flight request 18,879), preserve the policy and
last-request clock, and write immutable hold evidence before clearing this one
pending/stopped guard. No automatic repeat or recurring-unit change is added.
The original full-workload audit/activation gate remains closed while the held
request is unresolved; completion of the narrowed work cannot activate it.

Validation: eight isolated hold/reconciliation checks pass after correcting a
shared-object alias in one fixture; the original failing log remains retained.
The existing 20 selected-company collector tests pass in 17.792 seconds.
The exact narrowed manifest and checkpoint passed the established active-binding
validator with zero provider calls. Canonical immutable counts at 21:28:42 UTC
show earnings, balance sheets, and cash flows captured for 2,201 symbols each;
full as-reported snapshots cover 406, income statements and segments four each,
estimates 2,182 per period, ratings 517–518, and targets 514. These counts are
stored-source coverage, not proof of unlimited historical completeness.

### Date encoding correction, recovery, and actual restart

Review of the 16 retained date failures found fourteen valid source encodings:
uppercase English month names, trailing spaces, day-first full-month names,
and an unambiguous slash date. The existing nested-date parser now normalizes
those encodings while preserving raw payloads, date-only precision, conflicting
date rejection, and missing-year/ambiguous-numeric rejection. The existing
source-date contract documents the exact rule. Source and test preimages remain
in the private preparation directory.

The research, statement-history, and selected-company test selection passed
35 tests in 30.760 seconds. The eight separate hold checks passed. Independent
review accepted both the exact guard transition and the fourteen-response
recovery, verifying all sixteen source bodies/receipts and sixteen pure date
boundary probes. The recovery published 14 snapshots / 187 source rows using
original captures and zero GETs; bounded canonical reads verified all fourteen
snapshots and original raw hashes. AORT and ATRC still lack document years,
and the prior superseded BATRK cash-flow capture remains rejected.

Only those fourteen completed-result values were overlaid on the prepared
checkpoint; its before-image remains retained. Independent artifact comparison
verified the exact overlay, unchanged pending work/accounting/held charge and
all 47 final proof pins. The one-use helper itself remained unchanged.

The shared account guard was reconciled at 21:43:30 UTC, preserving usage 18,879
and the one held uncertain charge. Continuation 6 started at 21:43:37 UTC under
PID 286119 with the original absolute deadline. At the 21:44:50 UTC post-launch
check, 128 new requests had settled, bringing the narrowed checkpoint to 14,408
requests and 23,207 pending units. Including the original pilot and held charge,
aggregate usage was 14,428. Three sampled fresh canonical snapshots and their
source hashes verified. No new uncertainty or source rejection was recorded in
those first two batches. No recurring unit or completion finalizer was activated.

Evidence: continuation-6-launch.json, continuation-6-postlaunch-verified.json,
date-variants-publication-verified.json and continuation-6-proof.json under
.local/company-collectors-expansion-20260911/. Operational progress remains in
data/.operations/collection/company-selected/backfill-20260911/continuation-6/.
The source recovery receipts remain under continuation-5/date-variant-reconciled/.

## September 11 — approved best-effort completion policy

The user's approval to implement and start the recommended policy supersedes
the earlier zero-retry restriction for these six company groups. The first pass
continues past individual source/transport failures. Temporary failures enter a
later pass with at most two retries per request and 500 retry attempts total.
All attempts retain their account charge; an unretained response consumes its
full reserved response size. The original aggregate ceiling remains 67,539
requests / 8 GiB, with the original boot and monotonic deadline
311489.150269794. Successes are never fetched again by this continuation.

Known HTTP 429/5xx responses pause admission, including diagnostic failures
without a retained body. Only this invocation's fully drained observed pause
can be cleared, after waiting, under the existing account lock. Usage,
last-request time and original attempts remain unchanged. Credential, storage
and accounting faults still stop. Sidecar churn repeats only the pure identity
read when both physical store files are unchanged; HTTP and publication are
not wrapped in retries.

Implementation uses selected_company_best_effort.py and an opt-in company
transport policy; the existing strict default and other collectors keep their
behavior. The weekday entrypoint uses this same bounded policy for all six
groups at 20:00 America/New_York, retaining its 50,000-request / 4 GiB / 12-hour
cycle limits. The finite worker performs the stored-source/canonical audit and
activates the already authorized timer after the queue and eligible retry pass
finish. Explicit source and mapping gaps may remain; fatal or pending work
cannot pass that activation gate.

Continuation 6 had published another 25 outcomes after its last progress
checkpoint. The new seed preserves 26,583 completed outcomes (including the
charged CHEF failure), 19 outer pilot outcomes, and 39 cached responses awaiting
publication. Exact charges reconcile as 26,641 requests, 3,456,970,948 retained
bytes plus an 8,388,608-byte unknown-response reserve. It has 11,033 first-pass
pending units, including those 39 cached responses, and one initial CHEF retry.
The seed retains all 14,280 earlier completed result payloads and all P6
checkpoint identities. No shared allowance reset or additional migration occurs.

Validation: 90 focused/adjacent tests passed; the exact checkpoint-accounting
change passed its 19-test recheck. Independent review found an unretained
429/5xx pause omission; its correction and four new regressions passed all 33
policy/parallel tests. The adapted audit's evidence merge reconciles all 26,602
prior logical outcomes with zero GETs and zero canonical writes. Both unit files
pass systemd-analyze verification. Finite preflight and source pins are in
.local/company-collectors-expansion-20260911/best-effort-preflight.json.
Current execution evidence belongs to backfill-20260911/best-effort/; the
independent gate and launch receipt must be inspected before claiming activation.

Independent verification accepted the final proof
79fb73b61f5a0920229f34843a1f6a048a9d5e43180cf75cefdfdd7bf0d1a38e,
rechecked all 27 pinned files, ran 19 policy tests and all four correction
regressions, and found no remaining launch blocker. The worker launched under
PID 330386 at 2026-09-12 02:04:23 UTC (September 11, 22:04 Toronto), with its
original deadline and saved charges. The started marker is at 02:04:36 UTC.
The launch and independent review receipts are best-effort-launch.json and
best-effort-independent-review.json in the private preparation directory.
The timer remains pending the automatic stored-data audit and activation step;
a launched backfill is not evidence that the timer is active.

At 02:06:26 UTC, the worker was alive with three new batches complete:
192 fresh requests and all 39 cached responses had succeeded. The aggregate
checkpoint was 26,833 charged/processed units, 3,479,701,794 retained bytes,
10,802 first-pass units plus one retry pending. The original 40 source rejections
and one held transport gap remained; no new failures appeared in these batches.
Six source/database samples (three cached, three fresh) verified original bytes,
capture times, source references and canonical snapshots through the approved
immutable procedure. This check issued zero GETs and no canonical writes.
Evidence: best-effort-postlaunch-verified.json in the private preparation root.
The backfill remains in progress; final audit and timer activation remain pending.
