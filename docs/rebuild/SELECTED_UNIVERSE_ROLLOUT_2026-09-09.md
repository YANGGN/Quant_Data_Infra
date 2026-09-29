# Selected-universe rollout — September 9, 2026

Status: **Partially live; remaining provider dependencies are explicit.**
The 2,248-member selection, storage, and Sharadar/SEC mappings are active.
SEC backfill processed every planned missing issuer pair: **2,182 covered issuers
represent 2,204 selected securities**, with 42 issuer gaps and two identity gaps.
Its expanded weekday refresh is enabled. Sharadar has **533 of 534** history
partitions published; one failed request awaits a bounded recovery decision.
FMP account limits and the full Equibles mapping/checkpoint transition remain
pending. Live Fetcher is restored. The complete multi-provider rollout has not
yet been achieved.

## Authority and source

The user approved applying storage, completing mappings and budgets, backfilling
missing history, and activating the live fetchers for the selected universe.
This decision supersedes the earlier planning-only and bootstrap-only boundaries
for this rollout. It does not extend the options, macro, or transcript-model
analysis scopes.

The source remains `Major Index Liquid_2026-09-08.csv`: **2,248** members,
SHA256 `9393c72160eae41fc3af10a16af4ecc5ab2ab8f8bb7a5d4130162fa9e4aff15a`.
Membership snapshot:
`collection_snapshot_18a66dfbec3a1ed017c87e9dabda9ccb`.

## Activated storage and identity

Applied the reviewed market migration 0012 and company migrations 0012–0015.
The canonical migration ledger and original fact counts were checked afterward.
Existing prices, issuer records, transcripts, FMP statements, and estimates were
retained. Reporting-wrapper errors after successful operations were reconciled
from the committed ledgers; migrations, membership import, and the successful
Sharadar mapping were not repeated.

| Provider | Resolved selected securities | Distinct provider subjects | Unresolved symbols |
| --- | ---: | ---: | --- |
| Sharadar SF1 | 2,245 | 2,223 | PFBC, PSQL, TOWN |
| SEC | 2,246 | 2,224 CIKs | NBN, TOWN |
| FMP | 514 reused from original retained provider identity | 514 symbols (513 with CIK) | 1,734 still unresolved |
| Equibles | Pending full instrument/provider associations | Pending | Not yet assessed across the full selection |

The 22 Sharadar alternate share classes have explicit reviewed associations:
the original Nasdaq `relatedtickers` field and the SEC issuer CIK agree.
For BATRK this distinguishes Atlanta Braves from an older Liberty Media
association. Separate securities remain separate; only issuer fundamentals
share a provider subject. SEC requests are deduplicated by CIK.

Nasdaq returned valid HTTP 200 mapping responses with a `Location` header.
The transport now treats only HTTP 3xx as redirects, while continuing to make
exactly one request and never following redirects. Original failed bootstrap
receipts are retained; the corrected interpretation is separate evidence.

## Finite backfill execution

**Sharadar:** all six dimensions ARQ, ART, ARY, MRQ, MRT, MRY, every returned
column, and the SF1 indicator definitions. The complete history has no report-date
filter. The manifest contains 534 initial partitions of at most 25 provider
tickers. The invocation ceiling is **800 requests, 1 GiB, two hours**, with
one-second spacing and no retries. Both metadata responses reuse the earlier
approved bootstrap. Definitions have already been published. Raw pages, original
capture times, AR/MR separation, and observed revisions remain in the dedicated
Sharadar tables.

**SEC:** the existing 513 selected issuers with both canonical source datasets
are excluded from historical reacquisition. The remaining **1,711 issuers /
3,422 requests** are frozen into 18 batches. The overall acquisition ceiling is
**8 GiB and two hours**, with one-second spacing and no retries; each batch has
at most 200 requests and 1 GiB. This stage retains responses privately and makes
no canonical writes while the Sharadar publisher runs. Complete issuer pairs
subsequently used the existing SEC publisher. This establishes supported CompanyFacts
and recent submissions-index coverage, not a download of every filing document.

At 20:35 UTC, Sharadar's original attempt 362 remained charged after a transport
failure with no retained response. Its terminal reconciliation preserved the
attempt and cleared only the in-flight reservation. A continuation completed the
173 untouched partitions without repeating the failed one or extending the
original two-hour window. Total acquisition was **535 charged attempts and
322,989,308 received bytes**, including the definitions request; **533/534 SF1
partitions** are canonical.

The immutable post-write check at 20:48 UTC found **360,359 observations,
360,359 versions and 360,359 heads** across 533 complete captures, with row totals
matching. Nonempty provider coverage differs by dimension: ARQ 2,186; ART 2,223;
ARY 2,222; MRQ 2,211; MRT 2,223; MRY 2,222. A successfully empty provider response
is distinct from an unacquired partition.

The held request is the ARQ batch from OUST through PBF listed in
`.local/universe-rollout-20260909/sharadar-request-362-recovery-proposal.json`.
The proposed recovery is exactly one GET, 16 MiB and 60 seconds, with no further
retry; it has not been executed. The full-baseline guard correctly prevents
daily Sharadar acquisition until the initial gap is resolved.

SEC canonical publication processed the exact 1,711 retained issuer pairs:
**zero new requests**, 1,669 newly successful pairs and 42 explicit issuer gaps.
The 513 already covered selected issuers were retained. Aggregate active
publication was 1,494.107507 seconds within the original four-hour allowance;
the documented validation/host pauses added no requests or retries.

All caps count attempts, not successful publications. Failed or uncertain
requests are retained and are not automatically retried. A resource cutoff does
not imply complete history.

## Scheduled Sharadar refresh prepared

The fixed worker is `quant_data.operations.sharadar_selected_refresh`.
The prepared timer is daily at **05:00 America/New_York** with no persistent
catch-up and no service restart. It uses the existing provider queue and physical
store locks. Its invocation is capped at **200 requests, 256 MiB, one hour**,
with one-second spacing and an internal daily ceiling of 1,000 requests shared
with the same Sharadar queue.

Before any scheduled request, the worker proves full initial coverage across
all six dimensions for the current membership/mapping. It uses the last fully
completed update-date checkpoint with a seven-day overlap, preserves pending
windows across resource stops, and advances the checkpoint only after every
partition succeeds. A long outage therefore starts at the retained checkpoint.
The Live Fetcher schedule and saved-run catalogs include the Sharadar job.

Installation, enabled state, next trigger, and actual scheduled execution are
separate observations; they are not claimed by the prepared unit files.

## Expanded SEC refresh activated

The existing weekday **07:15 America/New_York** timer keeps its cadence.
Its active service points to `quant_data.operations.sec_selected_refresh`:
at most **4,500 requests, 12 GiB and six hours** per normal invocation, with
one-second pacing, 200-request / 1 GiB sub-batches, and issuer-pair publication.
The currently mapped scope requires 4,448 requests for 2,224 CIKs.

Each date is one new current observation. Retained pages and completed issuer
publications are reused within that date, and failed/uncertain attempts are not
automatically retried. Invalid source issuers do not suppress independent
issuers. Partial, unattempted and failed counts remain visible and nonzero.

After independent worker review, one user-manager daemon reload at 21:08:41 UTC
loaded the expanded SEC worker. The existing service/timer are repository symlinks.
The manager reports the new module and no pending reload; the timer is enabled
and waiting for September 10 at 07:15 EDT. No manual service execution was triggered.
The old service failure belongs to the prior worker and remains historical evidence.
Sharadar units are still uninstalled because one initial partition is missing.

## Remaining dependencies and validation

FMP's account plan/rate/download limits remain unanswered. The accepted shared
allowance contract requires evidenced limits, a maintenance reserve, effective
time, and already-charged usage. No new FMP request or FMP schedule expansion has
been made during this rollout.

Equibles' September 9 retained headers advertised **10,000 requests/day**,
despite the reported 100,000 plan, with a September 10 00:00 UTC reset. These are
dated observations, not a fresh current allowance check. Paid activation still
requires reconciling provider limits and local charges. The existing 519-member
scope remains intact; paid checkpoint conversion and full mappings are pending.

Focused offline checks completed so far:

- Nasdaq Location-header correction and adjacent controllers: 22 passed,
  13.306 seconds.
- New Sharadar refresh and adjacent source controllers: 22 passed,
  19.256 seconds.
- Live Fetcher schedule/calendar projection: 32 passed, 0.557 seconds, after
  correcting two test assumptions for the added job and renderer argument.
- Existing saved-run recorder: 12 passed, 1.018 seconds.
- `systemd-analyze --user verify` accepted the prepared Sharadar service/timer.

These are focused rollout checks, not a new full-suite claim. Earlier complete
integration evidence remains in the expansion plan. No commit or push occurred.

Private finite manifests, original responses, activation receipts, and current
results are under `.local/universe-rollout-20260909` and
`data/.operations/collection`. Completed request roots must not be rerun to
repair a reporting error.

## SEC retained responses and publication pause

Acquisition finished at 20:57:02 UTC: **3,422 requests, 4,372,500,175 bytes,
3,792.371 seconds, zero retries**. All responses and original capture times are
retained. CompanyFacts returned HTTP 404 for PFBC (0001492165), OZK (0001569650),
AYA (0001826836), and BRAI (0002076975); their submissions responses were retained.

The zero-request publisher completed nine issuer checkpoints before a controlled
pause at the tenth issuer. A quiet immutable check found 523 company issuers
(the prior 514 plus nine) and zero running ingestion identities; the interrupted
transaction rolled back. Completed pairs will not be republished during continuation.

Query-plan evidence identifies repeated historical scans inside the existing SEC
ingestion completion trigger. A three-index forward migration is allocated as
company:0015_sec_completion_indexes and registry 2.80.0. The candidate passed
the required full inventory, correction rechecks and fresh independent review.
The 40-path reviewed patch was integrated at 02:52 UTC on September 10. The index
migration was applied at 02:56:58 UTC and verified at 02:58:29 UTC. Every existing
table count, trigger body and prior migration entry was preserved; foreign-key
violations remained zero.

The SEC deadline/error-boundary correction passed 41 focused checks in 34.307
seconds. An independent reviewer passed 12 worker checks in 10.874 seconds and
separately demonstrated writer-end deadline rollback, retained raw responses and
charges, zero-request resumption and exact semantic replay. One earlier test
fixture error and its corrected rerun remain recorded; they are not a clean-suite
claim.

Live Fetcher was reloaded at 21:48 UTC on its existing loopback port 8766,
from the stale registry 2.75 process to active registry 2.79. The health route
returned HTTP 200 and the current revision. A subsequent status-page request
returned HTTP 200 (229,088 bytes, 11.58 seconds). It reports the SEC timer and
correctly lists Sharadar under unavailable schedules because that timer is not
yet installed. Browser automation was unavailable; these are HTTP observations,
not visual verification.

The isolated SEC index candidate passed seven focused checks in 27.925 seconds.
Independent review confirmed the three-index scope, exact predecessor projection,
and preserved migration bytes. It caught 22 stale current migration-count assertions,
which were corrected to 54 while historical counts were preserved. A separate
full-suite attempt revealed 13 omitted unchanged source/fixture inputs in the
isolated copy; those were restored, and five Atlas fixture checks passed in
0.111 seconds. Both interrupted full attempts remain recorded with their original
logs and manifests. The complete 907-file full offline run began at 21:53:31 UTC and ended on
September 10 at 02:39:58 UTC: 2,080 occurrences, 2,078 passes, two fixture failures,
raw exit 1, and 17,058.613 runner seconds. The original result and log remain
unchanged. Only the binding-matrix test's local prepared-mode assumption and the
saved-run test's obsolete SEC argument assumption were corrected. Their complete
owning modules passed all 30 rechecks in 14.794 seconds. All 2,076 unique discovered
cases have passing evidence, with no uncovered or skipped cases. Independent
review verified all 907 final hashes, 53 unchanged predecessor migrations, exact
registry projection and all 40 patch preimages. This is a completed full inventory
with explicit correction rechecks, not an uninterrupted clean run.

## September 10 WSL recovery and SEC continuation

After the successful index activation, WSL stopped accepting processes. The user
restarted WSL and explicitly requested continuation. At 04:01 UTC the source
validation hashes still matched, no SEC publisher or Inspector was running, and
the existing SEC timer was enabled and waiting for 07:15 EDT with the selected
worker. No recurring service was manually triggered.

The first continuation preflight rejected a private inventory file with mode
0644 before any invocation/publication. Its permissions were tightened to 0600;
all exact input bytes and reviewed launcher bytes were preserved. The unchanged
launcher then began the 1,702 remaining issuer pairs, skipping the nine completed
pairs and reusing every retained response. It makes zero provider requests and
retains the original four-hour aggregate active-publication budget; the explicit
validation/host pause consumes no active publication time. Final completion must
be taken from the continuation result, not inferred from process startup.

The existing loopback Inspector was restored on port 8766 at 04:04 UTC.
Its health response was HTTP 200 with registry 2.80.0. This proves process liveness
only; it does not prove provider coverage or a successful future timer run.
The current SEC publication progress and eventual result are retained under
.local/universe-rollout-20260909.

The restored status route returned HTTP 200 (216,211 bytes, 17.922 seconds);
the browser panel was queued, without visual verification.

The first continuation stopped at 04:06:48 UTC after 197.585 active seconds.
There are 245 issuer checkpoints: 244 successful pairs including the original
nine, and one source rejection for ERIC (0000717826), whose response contains no
unsegmented sec-core-v2 facts. VICR (0000751978) hit a separate canonical conflict:
joint accession 0000019617-26-000336 has stored acceptance 12:35:14Z versus incoming
16:35:14Z on September 3. Its transaction rolled back and its failure audit is
preserved. The immutable timestamp guard is unchanged; VICR is held without retry.
At that point, the next bounded continuation excluded those checkpoints and
the held issuer, leaving 1,465 untouched pairs and 13,556.179493 active seconds
of the original allowance. All 3,422 original SEC acquisition charges remained
unchanged.


## September 10 — SEC completion and scheduled conflict holds

Retained publication finished at **04:36:50 UTC**. All 1,711 planned missing
issuer pairs were processed, with **1,669 successful new pairs, zero unattempted
pairs and no terminal dispatch stop**. Including 513 retained existing issuers,
canonical SEC coverage reaches **2,182 of 2,224 mapped issuers**, representing
**2,204 of 2,248 selected securities**. Source observations retain their original
September 9 capture times. The partial outcome records gaps, not unfinished
dispatch.

The quiet immutable verification at 04:39:14 UTC matched every successful
receipt to its canonical run. The company store contained 2,183 issuers (one
outside the selection), 5,399 SEC snapshots, 1,872,529 filing records and
2,069,984 SEC fact versions. No ingestion was running; the migration ledger
was unchanged. These counts describe supported CompanyFacts and filing-index
coverage, not every historical SEC filing document.

| Remaining SEC gap | Count | Recorded detail |
| --- | ---: | --- |
| No unsegmented sec-core-v2 facts in the retained source | 29 issuers | Outside the current core mapping; original responses retained |
| Invalid source fiscal-year field | 1 issuer | TNET, dei EntityCommonStockSharesOutstanding, shares row 21 |
| Immutable filing metadata conflict | 8 issuers | VICR, MKSI, CNS, QRVO, SPHR, RIVN, MBC, VNOM |
| CompanyFacts HTTP 404 | 4 issuers | PFBC, OZK, AYA, BRAI; submissions retained |
| Unresolved SEC identity | 2 securities | NBN, TOWN; no issuer request |

The 29 core-mapping gaps are ERIC, GGAL, OMAB, GMAB, GRFS, ETOR, MMYT, NU,
VIK, STNE, DOO, GFL, XP, JBS, PAX, SRAD, PICS, RNW, GRAB, WRD, PSNY, BSP,
SLBT, ELE, AIIR, IQMX, PSQL, SKHY and XPRO. Their classification was checked
against retained bytes with zero GET. Invalid fields and conflicting filing
timestamps were not rewritten to force coverage.

The initial proposed scheduled-conflict catch passed 17 checks but independent
review rejected it: it could attempt an already-failed canonical identity after
a resource pause or on the next date. Its candidate and evidence are retained.
The corrected four-file change adds durable, private conflict holds keyed by
the existing canonical run/semantic identity, shared by manual and scheduled
queues. Each hold requires the exact metadata-conflict class/message, original
source hashes/capture time, and the unchanged canonical failure audit. Holds
survive date changes and resource continuations. Generic conflicts, subclasses,
corrupt holds, audit failures and storage errors remain fatal. A crash before
hold persistence remains fail-closed.

The corrected candidate passed **42 focused checks in 29.143 seconds**, including
the complete SEC core/controller modules, real rollback and independent issuer
continuation, pause/resume, next-date and manual-to-scheduled reuse, and invalid
hold/failure boundaries. Fresh independent review accepted the exact candidate.
The unchanged run-ID expression was independently compared; this changes no
schema, registry, identity semantics, binding, quota, cadence or unit file.

At **05:07:22 UTC**, only these four reviewed files were integrated:
quant_data/company/sec_companyfacts.py,
quant_data/operations/collection_sec.py,
quant_data/operations/sec_selected_refresh.py, and
tests/operations/test_sec_selected_refresh.py.
The prior complete-suite evidence remains intact; a supplementary effective
baseline records this focused correction, without claiming another full run.

At **05:07:54 UTC**, exactly eight historical holds were seeded after separate
independent review. Read-only checks reproduced each existing metadata guard
through a SELECT-only adapter and matched its failed canonical audit and
retained source pair. Seeding made **zero provider requests, zero canonical
writes and zero publisher invocations**; all eight holds and unchanged audits
were verified afterward. Private records are under
data/.operations/collection/sec/sec-metadata-holds/. Do not rerun any historical
publisher or seed invocation to repair a report.

Final read-only checks at **05:09:50 UTC** confirmed unchanged SEC counts and
migration/audit evidence, all eight private holds, and the integrated source
hashes. The SEC service was inactive with MainPID 0 and no pending daemon reload;
its timer was enabled, active and waiting for **September 10, 07:15 EDT**. The
next normal clock execution will import the corrected worker. No manual service
trigger occurred. Sharadar's timer remained uninstalled.

The restored loopback Live Fetcher remained PID 3945. One final status request
returned **HTTP 200, 216,832 bytes, 9.428 seconds**. This is HTTP/process evidence;
no browser visual verification or future scheduled execution is claimed.

Final private evidence includes sec-retained-publication-continuation-v3-result.json,
sec-canonical-post-continuation-coverage.json, sec-validation-gap-inventory.json,
sec-scheduled-holds-focused-result.json, sec-scheduled-holds-independent-review.json,
sec-scheduled-holds-integration-result.json, sec-metadata-hold-seed-result.json,
and rollout-final-readonly-result.json in .local/universe-rollout-20260909/.

FMP has made zero new requests in this rollout. The pending FMP account-policy
answer and the one-GET Sharadar recovery decision have not been supplied.
Daily prices, dividends/splits/earnings, FMP statements and estimates, full
Equibles transcripts and expanded news matching remain prepared pending their
provider identities and budgets. Existing normal schedules retain their
authorized scopes. Options, macro and transcript-model analysis remain separate.
No commit or push occurred.
