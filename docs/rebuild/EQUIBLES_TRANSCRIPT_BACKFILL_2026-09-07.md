# Equibles raw transcript backfill — 2026-09-07

The user authorized storing raw transcripts for the existing 500+ ticker universe,
with at most 100 Equibles requests per day, finishing each ticker's full available
history before moving to the next. This supersedes the evaluation-only scope for
this new finite population. No other Equibles datasets or recurring units are included.

## Scope and execution

Freeze the 519 FMP equity symbol/instrument bindings already in the market store,
using its approved quiet immutable reader. These are captured provider associations,
not permanent ticker identities or inferred issuer/CIK mappings. Process alphabetical
tickers, then ascending fiscal year/quarter. Discover every EarningsCall event page
(maximum 100 per page, 1,000 events per ticker), retaining events without transcripts
as explicit coverage gaps. Fetch every advertised transcript, at most 200 turns per
page and 50 pages per call. Exceeding a bound or finding ambiguous fiscal identities
blocks that ticker instead of claiming full history.

The zero-argument module `quant_data.operations.equibles_transcript_backfill` uses
the fixed project, company store, and `data/.operations/equibles-transcripts`.
It uses the existing named `EQUIBLES_API_KEY` resolver and only Bearer-authenticated
GETs to two fixed `https://api.equibles.com/v1/stocks/{ticker}` endpoint shapes.
No redirects or automatic transport retries. Each GET is bounded to 8 MiB and
30 seconds; each invocation to 100 attempts, 400 MiB, and 3,600 seconds. Sequential
requests are separated by one second. No network occurs under a canonical store lock.

The daily budget is the lesser of the durable local 100-attempt UTC-day cap and
the provider's reported remaining quota. Reserve before network, including failed
or uncertain attempts. Evaluation receipts seed September 7 with 40 used and at
most 60 remaining; 15 existing catalogue/transcript responses are reused. Other
account users may reduce that remaining allowance. A 429 stops until the next UTC
day; quota deferral is the only automatic repeat of a returned unsuccessful request.
Authentication errors, transport failures, unexpected HTTP failures, malformed
quota headers and publication failures pause the backfill for reconciliation.
The September 8 user decision below supersedes the original transcript-404 pause:
confirmed 404s are retained as coverage gaps and advance to the next quarter.
A 404 with all quota headers absent keeps the locally charged attempt and allowance;
partial or malformed quota headers still pause. Successful and missing responses
are immutable, content-addressed and reusable without another provider request.

## Storage and time

Registry 2.74 adds only company-owned evidence dataset
`company.equibles.transcripts`, fixed private collector
`equibles.company.transcripts`, and company migration 0010.
Uncommitted registry 2.73 / company 0009 analyst work is a prerequisite and is
preserved. Exact predecessor projections preserve all older contracts; no public
tool or generated catalog changes are intended.

Complete calls are atomically published through the established physical company
store lock and ingestion coordinator. `company_equibles_transcripts` stores
source event metadata, original fiscal labels, frozen instrument association, and
complete-call counts; `company_equibles_transcript_pages` stores the exact original
JSON response bytes as BLOBs, ordered offsets, hashes, selected non-secret headers,
capture times, and source-artifact lineage. Partial calls stay in private evidence
staging until all contiguous pages validate. Exact semantic replay writes zero
canonical rows or runs. Both tables are append-only.

Local capture is the availability boundary. Raw call dates remain unverified source
metadata, never backdated research availability. Nullable speaker identities and
timings, corrections, punctuation and all raw fields are preserved. Guidance
extraction/normalization and a new public reader are outside this request.

## Scheduling and completion

The dedicated systemd timer runs at 00:10 UTC daily, ten minutes after quota reset.
Persistent scheduling makes one catch-up invocation when the host returns; durable
quota prevents a second allowance on the same UTC day. The service has no restart,
a 65-minute outer timeout, private file permissions, and writes only under data.
A separate resolved private job lock prevents overlap while canonical publications
continue to use the established physical database lock.

Progress and coverage receipts are retained in private state/status files; SQLite
is the authoritative transcript evidence store. Once the frozen universe is complete,
later invocations perform zero provider requests and zero canonical writes. New
tickers, periodic transcript refresh, and retries of uncertain failures require a
new scoped decision. The timer cannot run while the WSL host is off.

## Verification and activation

Required: focused pagination/quota/crash/replay/missingness tests, migration and
predecessor checks, full offline suite, fresh independent review, systemd verification,
company-only migration reconciliation, fixed-universe freeze and retained-evidence
checks, and bounded first-run/count/hash/lineage verification. Dated activation and
actual results belong in the operating envelope and the receipt below.

## Offline validation receipt — 2026-09-07

The required gate passed with **1,668/1,668 unique current test IDs** supported
by execution evidence across retained runs and affected-case rechecks, under
TEST_STRATEGY section 4. Runtime discovery returned 1,672 entries with four
duplicates. No missing, unexpected, or skipped IDs remain. This is reconciled
full-inventory coverage, not a claim that one uninterrupted command passed.
The final selected run recorded 265 passes and one stale current-version HTTP
assertion failure; the narrowly corrected assertion passed its exact recheck.
Earlier failures and interrupted logs are retained. A multiline completed success
corrected an undercount from 60 to 61 in one interrupted-run receipt.

Independent verifier `01a07a1a-2614-78e3-a0ba-8bee877fd3ce` approved the final
gate with stated limitations and no remaining review blockers or required rechecks.
The reviewer verified 13 current-head hashes, three analyst prerequisite hashes,
the company 0009 checksum, and the exact case union. The 14 Equibles tests cover
raw preservation, complete-page publication, no-change replay, daily quota,
crash recovery, request admission, and path/lock boundaries. The three initially
identified quota/crash/page-limit defects were corrected and independently
retested. Systemd user-unit verification and `git diff --check` passed.

Private evidence: `.local/equibles-combined-validation-gate.json`,
`.local/equibles-independent-verification.json`, and their linked original logs
and receipts. The reviewer-attested execution receipt is preserved separately at
`.local/equibles-combined-validation-before-attestation.json`, SHA-256
`1fd13fa4a042e07423c1fbd0257a7a562fad3b342aea0a4663e0e30bb4eb5bc8`.
Live application and first-run results are recorded separately below after the
agreed company 0009 pilot handoff.

## Activation receipt — 2026-09-07

The prerequisite FMP analyst task applied company 0009 at
`2026-09-07T06:50:51.596529Z` under its exact 2.73 projection, published its
26 retained pilot captures, and completed 20 no-change replays and 16 cutoff
checks before handing over a quiet company window. The Equibles company-only
runner then applied 0010 at `2026-09-07T07:01:09.081284+00:00` under registry
2.74.0. All nine predecessor ledger rows were preserved exactly; the registered
schema and foreign keys verified. The roster freeze completed at
`2026-09-07T07:02:11.105644+00:00`: 519 equities, 15 reusable responses,
and September 7 usage seeded with 40 requests and 60 remaining.

The first service execution ran from 07:03:51 to 07:06:19 UTC and finished
successfully. It made **60 new requests** and stopped at the daily quota:
100 total requests including the earlier evaluation, with zero remaining.
It stored **60 complete calls / 60 original JSON pages / 4,462 speaker turns /
3,511,798 raw bytes**.

| Ticker | Complete calls stored | Fiscal coverage | Checkpoint |
| --- | ---: | --- | --- |
| A | 26 | 2020 Q1–2026 Q2 | All available transcripts complete; one catalogue event has no transcript |
| AAPL | 27 | 2020 Q1–2026 Q3 | All available transcripts complete |
| ABBV | 7 | 2020 Q1–2021 Q3 | Resume remaining available history before advancing |

The immutable first-batch audit verified every raw BLOB against its content hash
and retained response, all complete bundles and artifact/snapshot lineage, zero
foreign-key violations, unchanged predecessor migration rows, and unchanged
counts for every pre-existing company table, including the FMP pilot.
No provider request or canonical write was made by this audit.

The two new units were linked to their reviewed project files. At
`2026-09-07T07:10:40.131847+00:00`, the dedicated timer was enabled and
active/waiting, with its next trigger **2026-09-08 00:10 UTC**,
or **September 7 at 20:10 EDT**. Its calendar is daily 00:10 UTC and
`Persistent=true`. The service was inactive after successful completion.
Enabling the timer left the attempt count at 60 new requests. No existing
recurring unit was restarted or reconfigured.

Execution evidence: `.local/equibles-activation.json`,
`.local/equibles-first-service.log`, `.local/equibles-first-batch-audit.json`,
`.local/equibles-unit-install.json`, and
`.local/equibles-timer-activation.json`. Durable ongoing checkpoints and quota
receipts are under `data/.operations/equibles-transcripts/`.
The canonical store is `data/company.sqlite`, in
`company_equibles_transcripts` and `company_equibles_transcript_pages`.
The remaining population is scheduled, not already populated. The WSL host
must be running; source availability and quality remain provider-dependent.


## Local Inspector connection — 2026-09-07

The later user request adds a read-only Company transcripts view, Data status
schedule/progress overlay, and Status calendar/progress panel. Calls can be
filtered by symbol and fiscal period, then opened as bounded source-ordered
speaker turns. Current progress and recorded quota come from the existing
private checkpoint; they never claim historical selected-day state or live
account usage. Daily execution uses the established observational run recorder.
The finite backfill scope, timer, quota and canonical publication are unchanged.
See the [Inspector contract](TOOL_PLATFORM_SPEC.md#equibles-inspector-integration-and-grouped-daily-news--2026-09-07).

The subsequent [transcript analysis design](TRANSCRIPT_ANALYSIS_CONTRACT_2026-09-08.md)
covers guidance, analyst concerns and management tone using Terra extraction
and Sol pilot review. Its schema is proposed; no model run or storage migration
is recorded by that design.

## Missing-quarter continuation — September 8, 2026 (September 9 UTC)

The user explicitly requested skipping missing quarters, proceeding to the next
quarter, and resuming the remaining daily quota. Advertised transcript pages
returning HTTP 404 now record an unavailable-quarter checkpoint with fiscal
identity, response hash, observation time and any retained partial-page paths.
The gap and next-quarter cursor are saved atomically. No incomplete call is
published. A ticker with these gaps finishes as `completed_with_transcript_gaps`;
the status receipt includes `skipped_quarters`. Catalogue events already marked
without transcripts retain their existing meaning.

The paused ADM 2023 Q3 response was HTTP **502**, without quota headers, at
`2026-09-09T00:14:48.659152Z`; it is not evidence of a missing transcript.
The authorized continuation allows one controlled retry of that saved request
followed by the existing ordered workload, with at most **13 new requests** in
UTC day **2026-09-09**. The previously charged 87 attempts are not refunded.
The original failure/checkpoint remains retained; no recurring-unit configuration
or schema migration changes are part of this correction. Another provider failure
still pauses instead of being silently classified as absent data.

Focused offline validation passed all 22 collector/publisher cases. Two initial
restart-test assertions incorrectly matched the following ticker's same quarter;
both were narrowed to ticker A and the entire focused module passed.
Logs and pre-change checkpoints are preserved under
`.local/equibles-missing-quarter-fix-20260909/`.

### Continuation result

The bounded continuation finished at `2026-09-09T01:23:03.708883Z`.
All **13 new requests** returned HTTP 200: 12 ADM transcript pages and the
ADP event catalogue. ADM 2023 Q3 succeeded on the controlled retry, so no quarter
was skipped during this run. The UTC-day ledger reached **100 attempts / 0
remaining**, retaining and charging the original 502 attempt.

The canonical store now contains **251 complete transcripts / 251 raw pages
across 10 tickers**; all 10 have finished their available transcript histories.
ADM increased from 14 to 26 calls. ADP is the next checkpoint, with 28 advertised
calls and none fetched yet. The current UTC day added 95 transcripts in total.

The immutable completion audit verified all 12 new calls' raw hashes, retained
bytes, complete bundles and artifact/snapshot lineage, zero transcript foreign-key
violations, preserved prior 239 transcript records and page manifests, and the
unchanged migration ledger. The live status projection reads 100 used / 0 remaining
and 251 stored calls. Three additional status-reader compatibility tests passed:
**25 focused tests passed overall**. The full suite was not repeated for this
bounded collector-policy correction.

Evidence: `resume-authorized.json`, `resume-result.json`,
`completion-audit.json`, focused logs and the task-specific source/checkpoint
baselines under `.local/equibles-missing-quarter-fix-20260909/`.
The audit's initial status assertion used “complete” for ingestion runs; the
verified stored value is “succeeded”. Its corrected immutable audit passed.
The existing daily timer retains its prior configuration; the collector is
unblocked and quota-deferred at ADP.

## Paid-plan and expanded-universe planning decision — September 8, 2026 (September 9 UTC)

The user selected the full 2,248-ticker Major Index Liquid CSV for transcript
coverage and reported upgrading Equibles to 100,000 API calls per day.
This supersedes the old 519-ticker/100-call target for the planned expansion,
without altering historical receipts, charged attempts or completed calls.

The [updated implementation plan](COMPANY_UNIVERSE_EXPANSION_PLAN_2026-09-08.md)
specifies explicit universe binding, migration of the frozen checkpoint,
preservation of partial/completed work and failed-attempt accounting, a
separate per-run budget, paid-plan quota reconciliation, bounded continuations
and compatible progress/status updates. Raw transcript expansion does not
expand model-based transcript analysis.

This is a planning record, not activation evidence. No service/timer, collector
constant, saved checkpoint, quota ledger or canonical data was changed by this
plan update. The paid entitlement has not yet been checked with a live request.

## September 11, 2026 UTC — expanded backfill and verified 10,000/day operation

Activated the 2,248 selected members after the user explicitly accepted the
freshly verified 10,000/day allowance and approved daily continuation at
00:10 UTC until the current available-history walk finishes. The earlier
100,000/day figure remains an unverified higher entitlement, not this job's
operating limit. Raw transcripts only; model analysis is outside this workload.

The new Equibles mapping resolves 2,222 exact listed symbols to existing
same-member instrument IDs. Four source/native spelling associations were
reviewed: BF.B/BF-B, BH.A/BH-A, BRK.B/BRK-B, MOG.A/MOG-A.
The following 26 members lack exact provider identity evidence in the retained
screener batches and remain explicit gaps:

ATLC, BATRK, BELFB, BF.A, BH, CENTA, DSGX, FOX, FWONK, GEF.B, GLIBK,
GOOG, HEI.A, LBTYB, LBTYK, LEN.B, LILAK, LLYVK, NBN, NWS, RUSHB,
SENEB, TOWN, UA, UHAL.B, Z.

These are identity gaps, not proof that transcript history does not exist.
No sibling issuer/share-class substitution was made. All 2,248 members remain
accounted for in the mapping; only the 2,222 evidenced subjects are dispatched.

Conversion preserved the prior 445 transcripts, 17 completed histories,
partially completed AIZ history, raw receipts and every charged attempt.
The 12 identity requests used 1,281,620 bytes and brought September 11's
shared charge count to 113 before bulk launch. The original remaining=0
under the legacy policy was retained as historical evidence; the first new
transcript response reverified the current provider quota.

Implementation:
`quant_data/operations/equibles_daily_backfill.py` composes the existing
checkpoint-aware runner under its continuation lock. Shared attempted calls
can never exceed the approved 10,000/day dispatch limit; lower fresh provider
remaining wins. The outer allocation is at most six hours, 4 GiB and
24 invocations; each inner invocation remains at most 1,000 requests,
400 MiB and one hour. The final dispatch checks include UTC day, charged
attempts and time remaining after reading the checkpoint. Failed/uncertain
requests are not retried automatically, and completed catalogue histories
are not restarted.

The existing zero-argument service entrypoint selects this controller for the
active binding. The timer cadence is unchanged at 00:10 UTC, with a service
timeout of 365 minutes. Today's separate dated process is limited to
September 11 and at most 9,887 additional calls; it writes its own completion
receipt and does not manually trigger the recurring service.

Initial running audit at 02:40:33 UTC: 534 canonical transcripts/22 symbols,
89 additional calls published, 21 selected histories complete, ALB underway,
no blocking error. History remains in progress; 26 identity gaps are outside
the executable roster. The original complete records and all 445 original
page hashes passed an immutable preservation check.

Validation and durable receipts are recorded in
[the operating envelope](CURRENT_OPERATING_ENVELOPE.md):
53 existing focused tests passed; 12 installed controller/entrypoint checks
passed; independent mapping/controller review and unit verification passed.
The final source, quota, activation, timer and progress evidence is retained
under `data/.operations/equibles-expansion-20260911/`.

## September 11, 2026 UTC - parallel acquisition activated

The user explicitly requested concurrent Equibles callers to accelerate the
expanded transcript backfill. The previously accepted operating allocation
remains 10,000 shared calls/day, at most six hours, 4 GiB and 24 invocations,
with no automatic failure retries. The 2,222 mapped subjects and 26 recorded
identity gaps are unchanged.

`quant_data/operations/equibles_parallel_backfill.py` now permits up to four
concurrent GETs, with starts at least one second apart globally. One owner
reserves all calls in the existing quota ledger before dispatch, retains each
response and drains the batch before the existing serial publisher advances
the checkpoint. Only advertised events for the current ticker are prefetched;
subsequent page offsets still depend on retained preceding pages. The existing
physical store locks, identities, canonical schema and publisher are unchanged.
Network workers use spawned HTTP processes and never open canonical stores.

Lower fresh remaining allowances stop queued callers; later out-of-order
headers cannot reopen them or increase saved remaining. Known undispatched
reservations keep their charges and settle without becoming uncertain failures.
Verified 429 responses defer at zero remaining. Headerless 404 responses do not
erase previously verified same-day quota. Retained batch journals permit
zero-GET settlement after interruption; uncertain or failed GETs remain held.

The apparent stall was a host-clock reversal at attempt 2026-09-11-382, whose
complete AMD 2020 Q2 response was retained at 02:46:10.659512 UTC, 0.361583 seconds
before its reservation. The last 100 calls before that stop averaged 31.29/min.
At 03:28:44.967462 UTC the existing publisher recovered the saved body using
an explicit later local re-observation proof. The original response bytes and
receipt timestamp were preserved; canonical availability is the conservative
later observation. Recovery made zero API requests and changed neither the
382 charged attempts nor the saved provider-quota observation. The checkpoint
advanced from 702 to 703 complete transcripts. This dated reconciliation is
replay-safe and applies only to that exact retained response.

The parallel continuation started at 03:29:09 UTC (PID 155361), with at most
9,618 additional charged calls, 4,281,206,196 unused bytes, 23 unused invocations
and the original 08:37:30.771799 UTC cutoff. It cannot reset the original
allocation or spill into another UTC day. Future normal clock runs use the
parallel controller through the existing service entrypoint. The installed
`quant-data-equibles-transcripts.timer` remains enabled at 00:10 UTC; its
service retains the 365-minute timeout and no automatic restart. No unit file,
timer cadence or binding changed during this acceleration.

The immutable running audit at 03:30:34 UTC found 761 complete transcripts and
761 pages across 31 symbols, 30 completed histories, and AMGN underway. All
702 prior capture records and page hashes were preserved. The 59 new complete
bundles (including the zero-GET recovery) passed raw-hash, retained-byte,
complete-bundle and succeeded-run checks, with zero transcript foreign-key
violations. The first 61 retained parallel responses were HTTP 200 and averaged
43.75 calls/min over 82.28 seconds. Four callers are configured; the observed
peak was two overlapping requests in this sample. This is running evidence,
not full-history completion or a guaranteed sustained rate.

Validation: 36 focused parallel/daily checks passed in 3.824 seconds, including
real offline spawned-worker execution, spacing, quota, uncertain attempts,
crash settlement and status-reader compatibility. Another 53 existing Equibles
checks passed in 100.648 seconds. Independent review closed five concrete
findings and passed six memory-only settlement/recovery boundary probes.
The reviewer authored neither code nor tests. The full offline suite was not
repeated for this bounded controller change; runtime checks used the existing
quiet immutable reader. No commit or push was made.

Evidence is retained under `data/.operations/equibles-parallel-20260911/`:
`final-preflight.json`, `independent-review.json`, `clock-reobservation.json`,
`clock-recovery-result.json`, `parallel-started.json`, `running-postcheck.json`
and the source/checkpoint/canonical baselines. `parallel-result.json` will be
written when this dated allocation ends. Per-batch reservations and outcomes
are under `data/.operations/equibles-transcripts/parallel/`; ongoing public
progress remains in the existing `status.json`.

## September 11, 2026 UTC - local publication lock stop and recovery

The user reported a stop near 3,600 API calls and requested investigation.
The completed dated result records a stop at 04:58:53.642029 UTC after 3,613
shared charged calls, with 6,387 local calls remaining. The cause was
`ConflictError: Timed out acquiring the physical store lock` while publishing
DRI fiscal 2026 Q4. The publisher used the store API's five-second default.
The parallel acquisition batch was already settled, with no pending or uncertain
provider attempt. Q2 and Q3 had published; Q4's HTTP 200 response, 93 turns and
complete single-page body were retained. The historical lock holder was not
recorded, so the timeout cannot be attributed to a particular other process.

The collector's existing failure policy saved a persistent block. A later
parallel invocation would reject that publication block because it is not an
unsettled acquisition guard. The enabled daily timer alone would therefore
not clear this exact stop. Neither the 10,000/day allowance nor a provider
HTTP failure caused it. The original dated allocation ended no later than
08:37:30.771799 UTC and had expired when the user requested investigation.

The narrow correction changes paid transcript publication to wait up to
60 seconds for the existing physical company-store lock, bounded by the
remaining invocation deadline. It changes neither shared locking primitives
nor provider retries, canonical semantics, timer cadence or resource caps.
Legacy calls without an invocation deadline retain their five-second wait.
Persistent contention can still produce a held failure; this change does not
automatically clear arbitrary publication or provider errors.

At 13:20:03 UTC the existing publisher recovered the exact retained Q4 response
without another GET. Its original captured-at value, raw hash and all quota
fields remain unchanged. Cached-only reconciliation then completed DRI's cursor
and stopped before requesting DTE. The immutable postcheck at 13:20:56 UTC found
3,812 stored transcripts, all 3,811 prior captures/page hashes preserved, the
new Q4 hash and succeeded ingestion verified, and zero transcript foreign-key
violations. There are 151 completed histories; DTE is next. The saved block is
clear and public progress records the expired runtime allocation.

Validation: 61 focused publisher, collector, parallel and daily-controller
checks passed in 17.313 seconds. New cases cover contention beyond the former
five-second wait, deadline exhaustion without mutation, and unchanged legacy
waiting/failure behavior. The full suite was not repeated for this domain-local
use of the existing bounded lock API. The original read-only audit initially
expected all three batch calls unpublished; the corrected assertion follows
the actual Q4 cursor and verified Q2/Q3 canonical rows. No mutation preceded
that assertion correction.

The user has been asked to approve a new dated continuation of at most 6,387
additional calls and three hours, retaining the shared 10,000/day ceiling,
four callers, zero API retries, and unused original byte/invocation limits.
At this record, no new network continuation has started. The existing authorized
00:10 UTC timer remains enabled and the recovered checkpoint can resume normally.
New finite approval is required only for restarting outside the prior dated
run's expired cutoff, under AGENTS.md's duration rule.

Evidence: `data/.operations/equibles-lock-stop-20260911/`, including
`investigation.json`, preserved state/journal/canonical/source baselines,
`retained-recovery-result.json`, `cache-reconciliation.json`, and
`recovery-postcheck.json`. Any later approved manual continuation must record
its own `resume-authorization.json`, `resume-started.json` and result.

### Explicit restart approved and running - September 11, 2026, 13:31 UTC

The user subsequently instructed, "pls restart the equibles backfill process".
This accepts the prepared continuation of at most 6,387 additional calls and
three hours, with four callers, the shared 10,000/day cap and no API retries.
The dated worker started at 13:31:36.310918 UTC (PID 216297), with its new
finite cutoff at 16:31:36.310918 UTC. It retains the original unused byte
allocation of 4,111,264,504 and 19 remaining invocations. All prior charges,
completed histories and the recovered DRI call are preserved. The existing
credential resolver, company publisher and daily timer are unchanged.

The immutable running audit at 13:32:27 UTC found 3,842 canonical transcripts,
30 added since this restart, 152 completed histories and DUK underway. The
first 34 retained responses were HTTP 200, with no blocking error. All 3,811
pre-recovery records/page hashes were preserved; the recovered DRI call and
30 new complete bundles passed raw-hash, retained-byte and completeness checks,
with zero transcript foreign-key violations. The shared ledger was at 3,649
charges and 6,351 remaining, including in-flight reservations.

This is restart and running evidence, not completed expanded history. The
61 previously passed focused tests were reused because the tested source
hashes are unchanged. The exact approval, limits and audit are retained as
`resume-authorization.json`, `resume-started.json` and
`resume-running-postcheck.json` in the operation directory above. The worker
will write `resume-result.json` when this finite continuation ends.

## September 11, 2026 UTC - evening Equibles quota continuation

The user explicitly requested resuming the unfinished Equibles backfill to use
today's 10,000-call allowance. This is a new dated continuation, ending before
September 12 00:00 UTC (September 11 20:00 Toronto), with at most 2,782 additional
charged calls, four callers, no automatic provider retries, and the unused
3,928,144,568-byte / 15-invocation allocation. The original 7,218 charges and all
2,222 mapped subjects / 26 identity gaps are preserved. The existing credential
resolver, company publisher, registry, canonical schema and daily timer are unchanged.

The earlier continuation stopped at 15:01:37 UTC on LUV 2023 Q2. Its retained
HTTP 200 response has the exact expected event/ticker/fiscal identity but zero
turns, totalTurnCount=0, offset=0, hasMore=false and empty data/corrections arrays.
This is an observed empty transcript, not an unknown network attempt. Its hash is
55c9fd7ad783a4c6e6db1e22f2e8e3e3896d277c038f6c705e863ea2096a1ae3.
The existing September 8 instruction to skip missing quarters was applied only
to this exact response: the checkpoint records provider_returned_empty_transcript,
HTTP 200, original observation time/hash and a recovery reference. No empty
canonical transcript was published and no provider request was repeated.
The two subsequent reservations have exact not-dispatched markers; their charges
were retained.

At 21:24:00 UTC the existing publisher recovered retained valid LUV 2023 Q1,
then the exact checkpoint advanced past the empty Q2 to Q3. Recovery used zero
GETs, preserved all 7,273 prior captures/page manifests and the migration ledger,
and brought canonical transcripts to 7,274 without changing quota. The original
blocked checkpoint, acquisition journal and response evidence remain preserved.
The shared parser and parallel failure policy were not broadened; a different
malformed or uncertain response still stops for review.

The dated worker started at 21:24:23 UTC as PID 281327. At 21:25:50 UTC the
immutable running audit verified 7,328 canonical transcripts, 55 new complete
bundles including the retained Q1 recovery, preserved all 7,273 prior captures
and page manifests, and found zero transcript foreign-key violations. Raw
hashes, retained bytes, complete bundles and ingestion lineage passed. The first
56 newly retained responses were HTTP 200. The ledger held 7,278 charges /
2,722 remaining, 295 histories were complete and LYB was in progress.

All 61 focused publisher/collector/parallel/daily tests passed in 13.047 seconds.
The exact recovery probe and seven rejecting boundary probes passed without
credentials or provider requests. The full suite was not repeated. No shared
implementation, unit, registry, migration, commit or push changed in this task.
This is confirmed resume/progress evidence, not completed daily quota or history.

Evidence: data/.operations/equibles-quota-resume-20260911-evening/
contains authorization.json, preflight.json, checkpoint-before.json,
journal-before.json, canonical-before.json, recovery-result.json,
recovery-postcheck.json, started.json and running-postcheck.json.
The dated worker writes result.json on completion and run.log during execution.
Its one-use helper is .local/equibles_quota_resume_evening_20260911.py.

## September 11, 2026 UTC - empty-quarter continuation implemented and restarted

The user explicitly requested updating the collector so an empty quarter advances
to the next quarter, then restarting the backfill. This supersedes the earlier
one-response-only LUV recovery boundary for this precise empty-quarter case.

The private serial collector now recognizes a successful transcript response
with the expected ticker, event ID and integer fiscal labels, an empty data
array, zero turn and total-turn counts, offset zero and hasMore=false. It
records provider_returned_empty_transcript with the real HTTP 200 status,
original response hash/time and fiscal identity, then atomically advances the
cursor using the established unavailable-quarter mechanism. It does not publish
an empty canonical transcript or claim complete coverage. The parallel response
validator accepts the same bounded empty case, including retained-batch recovery.
The strict canonical nonempty parser/publisher is unchanged. Wrong identities,
missing/invalid quota headers, contradictory pagination and empty later pages
remain held failures; there is no automatic failed-request retry.

The prior evening process ended at 21:54:31 UTC after 1,195 additional charges,
with 8,413 used / 1,587 remaining, 8,423 transcripts and 338 completed histories.
NI 2024 Q4 had returned the same valid zero-turn envelope; its two preceding
responses were complete and the following reservation was proven undispatched.

At 23:37:52 UTC the updated existing recovery path settled the retained NI
batch without any new GET, published its two complete neighboring transcripts,
recorded the Q4 gap and advanced to NI 2025 Q1. Prior charges and the conservative
remaining allowance were preserved. The source/checkpoint/journal baselines and
original responses remain retained.

A separate dated worker started at 23:38:32 UTC as PID 309356, using four callers,
at most 1,587 additional charges, 3,866,477,331 unused bytes and 13 invocations.
It cannot cross September 12 00:00 UTC (September 11 20:00 Toronto) or the existing
10,000 shared-call daily ceiling. The existing credential resolver, company
publisher, locks, binding and daily timer are unchanged. Future normal clock
runs load the updated collector; no timer/service was manually triggered or
reconfigured.

All 69 focused empty-quarter, publisher/collector, parallel and daily-controller
checks passed in 13.599 seconds. The original run's two new-test expectation
failures remain in focused-tests.log; corrected expectations use the controlled
fixture file time and allow valid retained-response quota-header reconciliation
while proving unchanged charges and conservative remaining. Regression tests
cover ordered skips, retained evidence, replay, crash recovery, existing blocked
batches, canonical rejection of empty calls, malformed data, partial-page stops
and quota-header failures. The full suite was not repeated for this private
collector change; canonical/schema/shared-contract code did not change.

At 23:39:24 UTC the immutable running audit verified 8,451 canonical transcripts,
all 8,423 prior captures/page manifests preserved, 28 added complete bundles
including the two zero-GET recoveries, and zero transcript foreign-key violations.
New bytes, hashes, complete bundles and lineage passed; migrations were unchanged.
NI completed with its explicit gap and NKE was in progress. The first 29 new
responses were HTTP 200; the ledger was 8,444 charged / 1,556 remaining.
This is verified running evidence, not completed daily quota or full history.

Changed implementation: quant_data/operations/equibles_transcript_backfill.py,
quant_data/operations/equibles_parallel_backfill.py. Regression coverage:
tests/operations/test_equibles_empty_quarters.py. No commit or push was made.
Evidence is under data/.operations/equibles-empty-quarter-fix-20260911/,
including original source/checkpoint/journal baselines, both test logs,
authorization.json, recovery-result.json, started.json and running-postcheck.json.
The dated worker writes result.json on completion; run.log retains its output.

## September 12, 2026 UTC - AVA retained-publication recovery and restart

The user explicitly requested restarting the backfill after the saved AVA
publication stop. The normal 00:10 UTC service had stopped at 03:09:34 UTC
(September 11, 23:09:34 Toronto) after a physical company-store lock timeout.
Its four AVA 2022 quarterly responses were HTTP 200, complete and already
settled in the acquisition journal; no pending or uncertain request existed.
The process had consumed 6,801 shared calls and 322,683,107 bytes in seven
invocations, leaving 3,199 calls in the current 10,000-call UTC-day allowance.

At 03:58:54 UTC the existing replay-safe publisher recovered all four retained
AVA responses without a provider request. Their original bytes/hashes and
capture times were preserved. The checkpoint advanced from 15,745 to 15,749
transcripts and cleared only the exact saved publication block. All prior
15,745 captures/page manifests and the migration ledger were preserved; quota
was unchanged. The blocked checkpoint and settled journal remain in the
operation's before files.

The requested separate dated continuation started at 03:59:38 UTC as PID
372199. It is bounded to September 12, at most 3,199 additional shared charges,
four callers, no automatic provider retries, 3,972,284,189 unused bytes and
17 unused invocations. Its hard cutoff is 06:10 UTC (02:10 Toronto), within
the original scheduled run's six-hour allocation. The current 10,000-call
ceiling and lower fresh provider remaining still constrain dispatch.
The existing credential resolver, publisher, locks, empty-quarter handling,
scope of 2,222 mapped subjects / 26 identity gaps and recurring timer are
unchanged. No recurring service/timer was manually started or reconfigured.

At 04:00:16 UTC the immutable running audit verified 15,771 canonical
transcripts, all 15,745 original captures/page manifests preserved, and 26
new complete bundles including the four zero-GET recoveries. Raw bytes/hashes,
bundle completeness and ingestion lineage passed, with zero transcript
foreign-key violations and an unchanged migration ledger. The first 24 new
responses were HTTP 200. AVA had completed; AVAH was in progress, 665 histories
were complete, and the ledger held 6,828 calls / 3,172 remaining. This is
running evidence, not completed quota or full-history completion.

The 69 passing focused checks from the empty-quarter fix were reused after
verifying unchanged tested source hashes; the publisher and daily-controller
hashes also match prior accepted validation. No shared implementation changed,
so tests and the full suite were not repeated. No commit or push was made.

Evidence is retained under data/.operations/equibles-ava-lock-resume-20260912/,
including authorization.json, checkpoint-before.json, journal-before.json,
canonical-before.json, first-retained-publication.json, recovery-result.json,
recovery-postcheck.json, launch.json, started.json and running-postcheck.json.
The original daily/status reports were preserved before launch. The dated
worker writes result.json when it finishes; run.log retains its output.
The one-use worker is .local/equibles_ava_lock_resume_20260912.py.


## September 13, 2026 UTC - ITGR lock recovery and daily quota completed

The user explicitly requested resuming the stopped backfill and using today's
full API quota. This authorized a separate dated continuation of at most 267
additional shared calls after the scheduled run stopped at 9,733 charges.
The continuation retained four callers, zero provider retries, the shared
10,000-call UTC-day ceiling, at most 30 minutes, 3,881,310,098 unused bytes and
14 unused invocations. It reused the existing named EQUIBLES_API_KEY resolver,
fixed 2,222 mapped subjects / 26 identity gaps and company.sqlite publisher.
No recurring service or timer was started, reset, or reconfigured.

The scheduled run's 04:57:51 UTC stop was a physical company-store lock
timeout on ITGR 2020 Q1. All four ITGR 2020 quarterly responses were HTTP 200,
complete, retained and settled, with no pending or uncertain request. At
15:30:33 UTC the existing replay-safe publisher and cache-only runner had
recovered all four without a GET or quota change. The checkpoint advanced
from 28,030 to 28,034 transcripts. Original captures, page manifests, response
bytes/hashes, capture times and the migration ledger were preserved.

The dated continuation finished at 15:38:26 UTC with outcome daily_cap:
267 new calls, 11,630,919 received bytes, one invocation and no provider
retry. Both the shared ledger and fresh provider headers confirmed 10,000
used / zero remaining. It added 247 complete transcripts after recovery,
bringing the canonical total to 28,281. There are 1,330 completed histories
and 892 remaining mapped subjects; JOBY is current. No saved block or pending
attempt remains. The enabled timer's next normal run was observed at
September 14 00:10 UTC (September 13 20:10 Toronto).

At 15:38:40 UTC the locked immutable completion audit preserved all 28,030
prior capture records and page manifests, validated all 251 new complete
bundles including the four zero-GET recoveries, checked raw hashes, retained
bytes and ingestion lineage, and found zero transcript foreign-key violations.
The migration ledger and source/binding/registry/unit hashes were unchanged.

All 69 focused publisher, collector, parallel, daily-controller and
empty-quarter tests passed in 16.261 seconds, with no skips. The one-use
preflight initially named a nonexistent page_offset column; it stopped before
checkpoint or canonical mutation and passed after using the existing
turn_offset column. No shared implementation or schema changed, so the full
suite was not repeated. No commit or push was made.

Evidence: data/.operations/equibles-itgr-quota-resume-20260913/ contains
authorization.json, preserved checkpoint/status/journal/canonical baselines,
validation-receipt.json, recovery-result.json, recovery-postcheck.json,
started.json, result.json and completion-postcheck.json. The one-use helper
is .local/equibles_itgr_quota_resume_20260913.py.


## September 15, 2026 UTC - KBH request recovery and explicit null continuation

The user requested retrying the blocked KBH ticker and moving on when a null
response is returned. This authorizes one controlled retry each for the
uncertain KBH 2024 Q3 and Q4 attempts, plus completion of KBH's remaining
advertised history: at most eight new GETs, 64 MiB, and 15 minutes on
September 15 UTC. The existing EQUIBLES_API_KEY resolver, 10,000-call shared
daily ceiling, fixed company.sqlite publisher, identities and physical locks
were reused. No automatic failure retry or recurring-unit change was added.

The collector now treats a literal JSON null first-page body, or a correctly
identified zero-count first-page envelope with data:null, as an explicit
transcript gap. It retains the exact body, HTTP status, hash, observation time
and fiscal identity, then advances. Validated data:[] handling is unchanged.
Null catalogue responses, malformed envelopes, invalid quota headers and null
responses after partial transcript pages still block; transport failure is
not proof of a null provider response. Empty/null bodies never become canonical
complete-call records. This is a bounded collector-policy extension under the
new user decision, not a shared schema or missingness-contract change.

The original September 14 attempts 129-132, blocked journal, checkpoint,
statuses and canonical baseline were preserved. Attempts 131 and 132 remain
uncertain and charged in their original UTC-day ledger. An explicit recovery
receipt authorizes the one-time retries; no response or cancelled-dispatch
marker was invented for either original attempt.

At 2026-09-15T03:21:40.337423Z the continuation completed KBH. Two retained
2024 Q1/Q2 pages were published without a GET. All eight new requests returned
valid transcript pages, including both retried quarters; no quarter needed
skipping. The operation received 452,574 bytes and added ten complete
transcripts. KBH now has 26 transcripts, complete available history, no
unavailable-transcript gaps and two catalogue events without a transcript.
Overall progress is 28,411 transcripts / 1,339 completed mapped histories,
with KBR next and no pending attempt or saved block. Fresh provider headers
and the local ledger showed eight used / 9,992 remaining for September 15 UTC.

The locked immutable audit preserved all 28,401 prior capture records and
page manifests, validated the ten new complete bundles against retained raw
bytes, hashes and ingestion lineage, and found zero transcript foreign-key
violations. The migration ledger, original quota/attempt records, membership,
prior completed histories, binding, registry and unit hashes were preserved.
Both repaired quarters replayed with zero GETs and zero canonical writes.

All 91 focused collector, publisher, empty/null, parallel, daily-controller,
paid-quota and transport tests passed in 95.103 seconds with no skips.
Six new null-response cases were included. Three mutation-free preflight
probes rejected changed pending-attempt, quota and cursor state. The full
suite was not repeated for this bounded local correction. Only the collector,
its focused tests, and these operational records changed; existing uncommitted
work was preserved. No commit or push was made.

The existing timer remained enabled/active/waiting, next observed trigger
September 15 at 20:10 EDT (September 16 at 00:10 UTC). Its service still
records the earlier failed scheduled invocation; that historical status was
not reset. The current progress file reports the successful manual recovery,
while daily-status.json retains the truthful earlier scheduled-run failure.

Evidence: data/.operations/equibles-kbh-recovery-20260915/ contains the
authorization, preserved baselines, recovery plan, cache recovery, three
bounded acquisition receipts, result, completion audit and no-change replay.
The one-use helper is .local/equibles_kbh_recovery_20260915.py; source/test
baselines and the complete test log are in
.local/equibles-kbh-recovery-20260915/.


## September 15, 2026 UTC - retained transport failure diagnostics

The service started at 03:31:29 UTC stopped at 03:35:46 UTC on KFY
2025 Q1/Q2. Attempts 121 and 122 have reservations but no retained response
or cancelled-dispatch marker. The run added 104 transcripts, reaching
28,515 total and 1,344 completed histories; UTC-day usage reached 122 charged
attempts with 9,878 remaining. The preceding KFY 2024 Q3/Q4 responses were
HTTP 200 and remain retained. The original worker discarded exception
details, so the exact transport cause remains unproven. No observed null
response explains this stop.

After the user challenged the earlier fix, local error handling was corrected:
the Equibles HTTP worker now reports an allowlisted failure category and any
received HTTP status, without response content, credentials or exception text.
The parent preserves typed timeout/connection/partial-response/worker failures;
serial and parallel acquisition retain failure receipts bound to the exact
attempt. Parallel settlement includes these diagnostics in its saved block
and recovers them without a GET or quota refund. Existing historical failures
receive no invented diagnostic. Null handling, request deadlines, quotas,
publication, automatic-retry policy and scheduler configuration are unchanged.

All 85 focused diagnostic, transport, parallel, empty/null, publisher and
daily-controller tests passed in 23.627 seconds. Eight new diagnostic tests
cover credential/error-text suppression, typed child-process messages,
persistent failure evidence, replay without retry/refund, and mismatched
receipt rejection. The full suite was not repeated for this bounded local
diagnostic fix. This change does not repair or prove the cause of the live
download failures and does not unblock KFY. The service remains stopped;
no provider call, credential read, operational-store access or restart was
performed for this diagnostic correction. Retry/continue behavior awaits a
separate user choice covering failed downloads.

Changed source: quant_data/operations/equibles_transcript_backfill.py and
quant_data/operations/equibles_parallel_backfill.py.
Focused tests: tests/operations/test_equibles_failure_diagnostics.py.
Baselines, full test log and validation receipt:
.local/equibles-failure-diagnostics-20260915/.

## September 15, 2026 UTC - one retry, unresolved coverage, and resumed service

The user accepted the offered "retry once, then mark unresolved and continue"
policy with "ok go ahead", including resumption within the remaining daily
quota. The subsequent "approve" authorized a WSL restart after the host became
unresponsive. WSL restarted successfully, and its existing user service session
and Equibles timer recovered. This newer decision supersedes the earlier
no-automatic-download-retry restriction for this existing finite selected
Equibles backfill. It adds no provider, population, credential, schedule, unit
configuration, or daily allowance.

The opt-in versioned checkpoint policy allows one retry after a retryable
download failure, then records an unresolved quarter and advances. Timeout,
connection, incomplete-response, lost-worker and historically uncertain
attempts qualify, as do HTTP 408/500/502/503/504. Two failed attempts exhaust
that path; durable original-attempt links prevent crash recovery from granting
another retry. A catalogue download exhausted after its retry records an
explicit unresolved catalogue and advances the ticker without claiming a
complete history. Retained partial pages remain evidence and are never
published as a complete transcript. Explicit first-page null/empty responses
continue to record gaps and advance under the previously approved policy.

Authentication, malformed identity/quota responses, credential exposure,
oversized responses and publication/locking failures remain blocking errors.
HTTP 429 retains the existing quota deferral behavior. Every reservation,
including a failed attempt or retry, remains charged. No cancelled-dispatch
marker or transport cause was invented for the two old uncertain KFY requests.
The service entrypoint now uses the canonical module's transport class so
typed failure categories survive execution through Python's -m entrypoint.

Activation preserved the original checkpoint, receipts, mapping, quota and
canonical baseline. Cache recovery added KFY 2024 Q3/Q4 with zero GETs, bringing
the total from 28,515 to 28,517. Attempts 121 and 122 remained charged and were
adopted as uncertain first failures. One start of the existing service was
issued at 04:18:09 UTC with at most 9,878 additional September 15 requests,
subject to the shared 10,000 daily ceiling, four callers, six hours, 4 GiB and
24 bounded inner invocations. Existing timer configuration was unchanged.

KFY 2025 Q1/Q2 both succeeded on their one retry, attempts 123 and 124. KFY
completed at 04:18:25.831545 UTC with 27 complete transcripts and no unresolved
or unavailable quarters. At the 04:19:30.642944 UTC integrity check, the service
had advanced to KLIC: 28,559 canonical transcripts, 1,346 processed mapped
histories, 170 charged requests, 9,830 remaining, two retry charges, and zero
unresolved quarters/catalogues. No terminal block was present. This is verified
ongoing progress, not completion of the selected universe.

The approved physical company-store lock and quiet immutable reader verified
all 28,515 prior captures/page manifests were preserved; all 44 new bundles
matched their retained raw bytes, hashes, complete counts and ingestion
lineage. The migration ledger was unchanged and transcript foreign-key checks
found no violations. Atomic checkpoint reads surrounded the live store audit;
the audit did not take the running collector's exclusive job lock. The old
attempt files, earlier quota days and completed histories were unchanged.
Both repaired quarters replayed with zero provider requests and zero canonical
writes. The existing service remained running with MainPID 2369 at 04:19:57 UTC.

All 115 focused retry, diagnostics, transport, parallel, empty/null, publisher,
daily-controller and paid-policy tests passed in 114.851 seconds with no skips.
Sixteen retry-policy tests cover success, exhaustion, replay/crash recovery,
quota/runtime bounds, retained HTTP failures, hard failure guards, partial
pages, null responses, catalogue failure, daily reporting and the service
entrypoint. The full suite was not run for this bounded collector correction.
The final source diff passed review and whitespace checks. Unrelated working
tree changes were preserved; no commit or push was made.

Changed source: quant_data/operations/equibles_failure_policy.py,
equibles_parallel_backfill.py, equibles_transcript_backfill.py and
equibles_daily_backfill.py. Added tests:
tests/operations/test_equibles_retry_continue.py. The earlier diagnostics and
null-response changes remain in place. Evidence, exact scope, pinned source /
binding / registry / unit hashes, activation, startup, live audit and replay:
data/.operations/equibles-retry-continue-20260915/. Test log and pre-change
baselines: .local/equibles-retry-continue-20260915/. One-use activation and audit
helper: .local/equibles_retry_continue_20260915.py.

## Equibles user-requested resume - September 15, 2026, 14:56 UTC

The user requested starting Equibles again with its remaining quota. The saved
checkpoint had 4,709 charged requests, 5,291 remaining, 32,861 transcripts and
1,583 processed mapped histories, with NTST's four-request batch interrupted.
The existing source, publisher, binding, registry and unit pins matched the
previously validated version. WSL completed its normal boot and user-session
startup during the checks; no host restart, unit configuration change or timer
change was performed for this request.

One start of the existing service at 14:56:42 UTC resumed the same finite
selection. The new invocation is limited to at most 5,291 additional requests
within September 15's shared 10,000-call ceiling, four callers, six hours,
4 GiB and 24 bounded inner invocations. The existing credential resolver,
company.sqlite publisher, physical locks and one-retry/then-unresolved policy
are unchanged. Original charges were not refunded.

The 14:58:06 UTC progress check found the service running (PID 3275): 32,893
transcripts, 32 additions, 1,584 processed histories and NU underway. NTST
completed with 24 transcripts. Its retained first response was reused; the
three uncertain requests recovered on their single retries, attempts
4710-4712. All four retained response statuses were 200 and their saved byte
hashes verified. Prior completed histories, original attempt files, earlier
quota days and the frozen selection were preserved.

There were 36 new charged requests, 4,745 total charged and 5,255 conservatively
remaining; fresh provider headers were observed at 14:58:03 UTC. No unresolved
coverage was recorded. The checkpoint's ParallelReservation marker represented
the next active batch, not a terminal failure. These are startup/progress
observations, not a completed-run or full canonical-integrity audit.

Evidence: data/.operations/equibles-user-resume-20260915T145642Z/ contains the
authorization, original checkpoint/status/journal/attempts, start receipt and
progress verification. The prior 115-test evidence was reused because the
validated source pins were unchanged; no tests, commit or push were added.


## September 16, 2026 UTC - user-requested Equibles lock-timeout resume

The user requested resuming Equibles. The normal 00:10 UTC invocation stopped
at 03:07:39 UTC with exit 75 after a 60-second physical company-store lock
timeout while publishing VAC. Its four-response batch had settled successfully;
VAC 2020 Q1/Q2 were published and Q3/Q4 remained in the retained cache.
There was no quota exhaustion: 5,252 requests were charged and 4,748 remained.
The former lock holder was not established. A first immutable preflight rejected
a changing sidecar; a subsequent locked immutable read and full transcript
metadata baseline succeeded without bypassing the read safeguard.

This explicit decision authorized one start of the existing service for the
remaining 127 histories in the frozen 2,222-subject selection, with at most
4,748 additional requests on September 16 within the shared 10,000-call cap.
The existing EQUIBLES_API_KEY resolver, company.sqlite publisher, physical locks,
four callers, six-hour/4-GiB/24-invocation ceilings, UTC-midnight deadline, and
one-retry/then-unresolved policy were preserved. No unit or timer was changed.

Only the exact recorded lock-timeout block was cleared after preserving the
original checkpoint, journal, attempts and response receipts. Cache-only
recovery used zero provider requests and published the two complete retained
VAC calls. The locked immutable audit verified 42,889 canonical transcripts,
preserved all 42,887 earlier capture/page manifests, checked both added raw
bodies against retained bytes and ingestion lineage, found no transcript
foreign-key violations, and confirmed an unchanged migration ledger and quota.

The single service start at 23:11:25 UTC succeeded with PID 663459. At
23:12:10 UTC the running checkpoint had 42,911 transcripts (24 additions,
including the two recovered calls), 2,096 processed histories and VAL underway.
There were 27 new charged requests, 5,279 charged for the day and 4,721
conservatively remaining. Twelve fresh retained response receipts passed their
hash/byte checks; provider headers were observed at 23:12:08 UTC. The active
ParallelReservation marker represented an in-flight batch, not a terminal block.
Prior completed histories, frozen selection, original attempts and prior quota
days were preserved. This is a verified startup/progress snapshot, not completion
of the remaining population or a claim that future lock contention is fixed.

Production source, registry, binding and unit pins matched the September 15
validated activation, so its 115 focused test results were reused; no new tests,
production code, commit or push were needed. The private one-use recovery helper
passed syntax and review. Evidence: data/.operations/equibles-user-resume-20260916/
contains authorization, original state/receipts, split canonical baselines,
cache recovery and integrity results, service start receipts and running
postcheck. Helper: .local/equibles_user_resume_20260916.py.


## September 17, 2026 UTC - requested AVB, EA and EQR run; provider unavailable

The user explicitly requested an Equibles run for AVB, EA and EQR, the three
retained FMP equity identities outside the selected 2,248-member CSV. The
one-time workload was bounded to 300 shared charged attempts, 30 minutes,
128 MiB, the existing EQUIBLES_API_KEY resolver and company publisher.
No recurring unit, binding file, registry or production source changed.

The original selected history was complete: 2,222 mapped subjects and 45,290
transcripts. A locked immutable preflight found no saved AVB/EA/EQR transcripts,
verified their original retired-roster identities and captured a transcript
metadata baseline. Pure checkpoint conversion/restoration checks preserved
membership, completed records and quota. The historical activation configuration
was used to verify the frozen selection hash; current configuration changes
had not changed its membership or provider mapping. These preparations made
zero provider requests or canonical writes.

The first identity reservation was rejected by the history transport's local
route check before network dispatch. Its charge and diagnostic were retained,
with a separate not-dispatched reconciliation receipt. The existing bounded
identity helper then made one screener request at 02:45:20 UTC, returning
HTTP 200 with an empty result and notFound=[AVB, EA, EQR]. No positive provider
mapping was invented and no checkpoint selection transition was activated.

Three separately bounded direct earnings-call catalogue checks followed at
02:48:14-17 UTC, one per explicitly requested ticker. Each returned HTTP 404.
The run therefore added zero transcripts and made zero canonical writes.
Original checkpoint fields and the selected scope were unchanged except for
shared quota accounting. The complete status was refreshed cache-only with
zero provider calls. Retained response bytes matched all three saved hashes.

Total cost: four actual provider requests and five conservative local charges,
including the proved pre-dispatch rejection. September 17 accounting reached
996 charged and 9,004 locally remaining; the last provider header reported
9,005 remaining. There were no provider retries. Existing 115-test evidence
was reused for unchanged production pins; the private helpers passed syntax
and scoped preflight checks. No new suite, commit or push was needed.

Evidence: data/.operations/equibles-three-tickers-20260917/ contains authorization,
original checkpoint and split canonical baseline, helper validation, original
and continued launch receipts, route-validation reconciliation, identity
response, each direct catalogue response, direct result and completion.json.
Private helpers: .local/equibles_three_tickers_20260917.py and
.local/equibles_three_direct_catalogues_20260917.py. These results establish
unavailability under the three exact requested Equibles ticker routes at the
recorded time, not absence of historical transcripts from every possible source.
