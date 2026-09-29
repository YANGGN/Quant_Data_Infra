# Shared universe and collection expansion implementation plan

Status: Implementation in progress. The collection foundation is under offline verification; expanded operational collectors are not active.
Revision: Full collection matrix, preserving the newer FMP/transcript decisions.
Decision date: September 8, 2026 America/Toronto (September 9 UTC).
Integration owner: the primary agent; migration ordinals are not allocated here.

## 1. Updated user decisions

Use Major Index Liquid_2026-09-08.csv as the shared equity selection for Sharadar
fundamentals, daily prices, SEC filings/CompanyFacts, dividends, splits, earnings
dates, FMP statements and estimates, and Equibles earnings-call transcripts.
News uses this selection for symbol matching with source-specific request plans.
Other distinct FMP inputs have an explicit endpoint-by-endpoint expansion review.
The selected input contains 2,248 unique source tickers, not necessarily 2,248
distinct issuers. Retain existing ETF/index selections alongside the new equity
price selection. Options and macro retain their independent scopes.

The user accepted the remaining collection recommendations and requested that
all appear in this implementation plan. This supersedes the prior deferral of
price, SEC, corporate-action and news scope changes. The older table's targeted
FMP statements and initial 519-ticker transcript suggestions are superseded by
the user's more specific full-universe decisions below; they are not reinstated.

FMP statements and estimates are now intended to cover every supported member,
rather than being limited to targeted supplemental collection. Equibles coverage
is likewise intended to expand beyond the old frozen 519-ticker population.

The user reports an upgraded paid Equibles allowance of 100,000 API calls per
day. Use that as the requested daily ceiling in the new implementation.
It has not been verified against live account headers. The older 100-call
receipts describe their original dates and remain unchanged.

This plan does not start a provider run, change a recurring unit, migrate a
canonical store, or repeat a completed population. Execution manifests will
state the concrete units and finite request/byte/time bounds before acquisition.
The account allowance is a ceiling, not a request to consume 100,000 calls.

Related contracts: [Sharadar storage](SHARADAR_STORAGE_DESIGN_2026-09-08.md),
[FMP statement storage](FMP_RESEARCH_INPUTS_CONTRACT_2026-09-06.md),
[FMP analyst history](FMP_ANALYST_HISTORY_2026-09-07.md), and
[Equibles transcripts](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md).
Existing operational scopes and activation evidence remain indexed in the
[operating envelope](CURRENT_OPERATING_ENVELOPE.md); shared scheduling and
publication mechanics follow [locking](SCHEDULING_AND_LOCKING.md).

## 2. One selection, explicit collector bindings

The intended steady-state workflow is simple: update a universe snapshot,
resolve its provider mappings, and let explicitly bound collectors plan their
missing work. An instrument's existence must not itself schedule acquisition.

The current implementation cannot reach this state by changing one table:

| Current dependency | Required change |
| --- | --- |
| Stage10 universe IDs are closed; snapshots allow at most 800 members | Add generic market universe, snapshot and membership relations through a forward migration |
| Stage10 instruments are FMP-specific | Reuse existing instrument identities through evidenced provider mappings; do not create a second identity for an existing share class |
| SEC and company-market refreshes select all Stage10 equities and cap at 700 | Add explicit selected-universe bindings, issuer resolution and revised request/byte/runtime budgets; expand each collector at its planned transition |
| Market-close selects all Stage10 equities/ETFs/indexes and caps at 800 | Bind the union of selected equities and retained ETF/index snapshots; adapt roster/scope contracts and finite budgets without changing price semantics |
| News coverage selects Stage10 instruments and caps at 800; Alpaca batches contain at most 50 symbols | Resolve the larger matching roster and build source-specific bounded batches while retaining fixed global feeds |
| Equibles freezes exactly 519 instruments and uses a positional cursor | Migrate checkpoint state and bind a versioned universe without restarting completed work |
| Equibles status readers accept at most 519 tickers and 100 requests | Version progress contracts and update bounds, reporting and compatibility checks |
| Company-market refresh combines actions with one annual-estimates page | Give expanded annual/quarterly estimates an explicit collector scope and authoritative dataset; avoid duplicating it across writers |

Implementation uses market_collection_universes, market_collection_snapshots and
market_collection_members. The existing fixture-era market_universes relations
remain unchanged. Provider assertions use independent market_collection_mapping_snapshots
and market_collection_provider_mappings, with immutable acquisition evidence. Retain original membership labels and source evidence;
later identity assertions are separately versioned. Register collector bindings
in the existing machine-readable registry/configuration instead of creating an
independent configuration authority. Every run pins a snapshot and mapping version.
Changing membership during a run does not alter that run's workload.

All collection rows in Section 4 are part of this plan. Preserve each currently
running binding until its explicit expanded replacement is ready, then transition
it without duplicate writers or dropped history. This is rollout sequencing,
not a deferral of the accepted coverage. Keep ETF/index selections independently
versioned; options and macro do not inherit the equity roster.

For other FMP inputs, review and enumerate recommendation/grade history, current
recommendation distributions and consensus, price-target history/consensus/summary,
and product-revenue segments as separate supported expansion candidates. Measure
entitlement, historical/current meaning, completeness and workload for each before
adding it to a finite acquisition manifest. Sharing a publisher is not authority
to call every endpoint. This implements the accepted recommendation to consider
broader coverage separately, rather than silently omitting that workstream.

## 3. Resolve identities and preserve completed coverage

The retained immutable baseline has 519 equity instruments. The CSV matches
514 symbols exactly, has two proposed aliases (BRK.B/BRK-B and BF.B/BF-B), and
leaves 1,732 potential additions after those aliases are verified. AVB, EA and
EQR are absent after those mappings; retain their instruments and historical
facts without adding them to this selected snapshot.

Pin the original CSV and SHA-256
9393c72160eae41fc3af10a16af4ecc5ab2ab8f8bb7a5d4130162fa9e4aff15a.
The filename date is a label, not proof of historical membership availability.
Never globally replace ticker punctuation, merge share classes, or invent CIKs.

Build mappings for FMP, Sharadar and Equibles independently, plus issuer/CIK
links where evidenced. Existing FMP publishers require an existing issuer/CIK;
resolve that dependency before publication and report unresolved members.
Sharadar source subjects can remain explicitly unmapped under its own contract.
Deduplicate acquisitions only when the provider subject and data semantics are
proven equivalent; sharing a company name is insufficient.

Before acquisition, inventory retained source evidence and coverage through the
approved bounded immutable readers. Reuse complete price/date windows, SEC
submissions/CompanyFacts evidence, corporate-action and earnings records, FMP
estimate histories, and Equibles calls already captured. The September 7 analyst receipt reports data
for 516 of 519 symbols; this is historical evidence, not a fresh completeness
check for the selected snapshot. Statement coverage has a separate inventory.

Plan the set difference by provider subject, endpoint, period/dimension and
completed request coverage. Reuse complete retained responses and original capture
times. If an API cannot express a missing historical slice, enumerate any required
overlapping request explicitly; do not silently refetch completed populations.
Fresh incremental observations are distinct, dated acquisition units.

## 4. Target data and storage

| Dataset | Intended coverage | Owning database and source tables |
| --- | --- | --- |
| Sharadar SF1 | Available history for mapped selected subjects, ARQ/ARY/ART/MRQ/MRY/MRT, all returned fields | company.sqlite; new company_sharadar artifact, capture, observation and version relations |
| Daily prices | Supported selected equities plus independently retained ETF/index selections; missing historical windows and ongoing EOD updates | market.sqlite; reuse existing daily-price evidence/version/current families with additive identity/scope compatibility where required |
| SEC filings and CompanyFacts | Supported issuers represented by the selected universe; one acquisition per CIK, endpoint and required page/window per run | company.sqlite; existing company_issuers, company_sec_artifacts, company_sec_filings, company_sec_fact_versions and related snapshots/mappings |
| Dividends and splits | Available history and changed/new events for supported selected members, after request/runtime sizing | company.sqlite; existing company_action_source_artifacts, company_action_snapshots and company_corporate_action_versions |
| Earnings dates | Available historical and upcoming events for supported selected members, with source corrections and timing precision | company.sqlite; expanded FMP earnings endpoint in the analyst-history source family, preserving existing company_earnings_event_versions and its contracts |
| FMP statements | Available annual and quarterly income statements, balance sheets, cash flows, and supported full as-reported statements for mapped selected members | company.sqlite; existing company_fmp_research_snapshots and company_fmp_research_rows, extended additively only where required |
| FMP analyst estimates | Available annual and quarterly target periods for mapped selected subjects, plus prospective captured revisions | company.sqlite; existing company_fmp_analyst_captures, company_fmp_analyst_observation_versions and membership relations |
| Other distinct FMP inputs | Separate review of recommendation/grade data, price-target data and product-revenue segments for broader supported coverage; exact endpoints and budgets are enumerated before acquisition | company.sqlite; existing analyst-history or research-input families according to source contract |
| News | Match the selected universe while retaining existing ETF/index matching where supported; size symbol-based source batches independently | news.sqlite; existing source captures, article versions and dated symbol-association/reader contracts |
| Equibles transcripts | Every advertised, available earnings-call transcript for mapped selected subjects, with complete pagination and the reported 100,000-call/day ceiling | company.sqlite; existing company_equibles_transcripts and company_equibles_transcript_pages |
| Options and macro | Existing independent scopes and schedules; no inherited equity-universe expansion | Existing market options datasets and macro.sqlite datasets |
| Universe and security/provider mappings | All 2,248 source members, including unresolved mappings, plus separate ETF/index selections | market.sqlite; generic membership plus existing/extended identity contracts |

Use the existing analyst-history family as the authoritative expanded FMP
estimate dataset. Preserve older research-input/expectation captures and their
reader contracts; do not republish equivalent estimates into multiple families.
Separate the current actions wrapper's estimate dependency before transitioning
that step, and expand its dividends/splits scope through its own binding and
budget. Expand FMP earnings observations through the analyst-history source
family rather than introducing a second writer of the same source observations.
Keep existing canonical earnings readers compatible via explicit mappings.

The existing FMP statement parser limits a response to 100 rows and 1 MiB.
Verify each endpoint's supported history/pagination contract; split work or extend
validated bounds where necessary. Reaching a parser or page cap is an explicit
incomplete outcome, never proof that full history was fetched.

Preserve original response bytes, request scope, source schema, hashes and capture
times. Never insert Sharadar rows into SEC-specific fact/normalized tables.
A common read service exposes source, reporting basis, period, currency, selected
version and lineage, with explicit fallback and comparison policies.

Changed rows append versions; exact semantic replay writes nothing. Preserve
Sharadar AR/MR separately and retain every changed state observed locally.
Historical FMP estimate target periods are not historical estimate vintages.
Transcript capture time remains its availability boundary. Complete transcript
bundles publish atomically; partial pages stay as retained resumable evidence.

### 4.1 Daily prices

Freeze the exact union of selected equities and retained ETF/index snapshots,
deduplicated by instrument identity. Do not replace the ETF/index selections
with the CSV. Derive counts from those snapshots rather than assuming the
historical ETF/index counts are still current. Preserve original prices for
previously covered instruments outside this new equity selection.

Backfill newly covered instruments and explicitly missing date windows in a
finite historical range aligned with the established price dataset. Retain
provider, price variant, currency and adjustment meaning; adding Sharadar
fundamentals does not switch the existing price provider. Adapt identity/scope
validation and publisher/read dependencies as well as the 800-instrument limit.
Do not rerun frozen Stage10/12 population units or reset existing price heads.

The expanded market-close worker needs bounded batches, durable progress and
per-symbol outcomes. Preserve current-session/calendar rules, sentinel behavior,
reviewed noncoverage handling and physical-store publication. A failed or
unsupported symbol cannot be reported as complete price coverage. Historical
backfill and daily current-session maintenance have separate request manifests.

### 4.2 SEC, actions and earnings

Resolve supported issuers first. Fetch submissions and CompanyFacts once per
distinct CIK/request identity and link the results to all evidenced securities.
A single issuer represented by multiple share classes must not multiply SEC
requests. Preserve accessions, filing dates, taxonomy/concept/unit distinctions
and actual source/local availability. SEC ingestion can establish issuer
identities needed by the existing FMP publishers, so it precedes their new-issuer
publication. Unresolved/non-SEC subjects remain explicit coverage outcomes.

Verify the current adapter's submissions-history boundary. If older submission
index files are needed for planned filing-metadata coverage, list them as bounded
units; do not describe recent submissions alone as full filing history or as
stored filing-document text. Full document acquisition, if selected, requires
its own enumerated document list rather than being inferred from CompanyFacts.

Expand dividends, splits and earnings dates for supported members. Preserve
declaration/ex/record/payment dates, split ratios, source event identity and
earnings announcement timing without inferring missing time-of-day precision.
Share-class-specific dividends/actions remain security-specific even when SEC
facts share a CIK. Empty responses do not delete old events. Inventory existing
events and acquire gaps plus new/corrected observations.

Replace the 700-equity assumptions with selected-subject bindings and sized
batches. Recalculate per-endpoint and aggregate request, byte, deadline and
runtime budgets; do not merely multiply the existing constants by 2,248/519.

### 4.3 News and other FMP inputs

Expand the matching roster to all resolved selected securities while retaining
existing ETF/index associations where supported. Keep provider symbols and
dated identity mappings explicit. Global/Fed/ECB/BEA/EIA/website feeds remain
shared source requests; they are not repeated once per ticker. Preserve raw
articles and source versions when adding or revising symbol associations.

The existing news roster caps at 800. Replace its scope dependency and size the
source-specific request plans. With the existing 50-symbol Alpaca batch limit,
2,248 supported equity symbols require 45 batches before retained ETFs and any
provider exclusions; this is planning arithmetic, not a live entitlement or
complete-feed-coverage claim. Include other feeds in the aggregate budget.
Keep request symbol limits distinct from response/article limits; check
pagination and time-window completeness for every source and report truncation.
Retain the existing hourly cadence unless measured runtime requires an explicit
schedule change. This expands current-news matching/acquisition; it does not
silently add an unlimited historical news backfill.

For distinct FMP inputs, record the decision per endpoint family: expand with
a named budget, unsupported/unentitled, or deferred with a reason. Review
recommendation events and distributions separately from current consensus;
review dated price targets separately from current summaries. Product segments
retain source categories and bounded fiscal periods. Preserve existing data
and do not infer historical vintages from current snapshots.

### 4.4 Cross-collector budgets and unaffected scopes

FMP statements, estimates, prices, actions, earnings and relevant news requests
share one provider allowance. Plan their combined workload and pacing, with
independent resumable queues and prioritized current maintenance. Equibles'
paid allowance does not change FMP, SEC, Nasdaq or Alpaca limits. Verify and pin
actual provider limits during bounded preflight rather than inventing capacity.

Preserve the current options selections and macro series/calendars. Their
collectors must not acquire additional underlyings or series just because the
equity catalog grows. Keep news, market and company ownership and locks distinct;
network work precedes short store-local writes, and cross-store progress is
explicit rather than claimed atomic.

## 5. Equibles paid-plan and checkpoint migration

Change the daily ceiling to 100,000 while separating daily allowance, requests
per invocation, byte limits, runtime limits and instantaneous pacing. Initially
retain sequential requests and existing one-second spacing, 30-second per-request
deadline and 8 MiB per-response limit. A daily allowance does not establish a
per-second allowance; faster pacing needs documented provider limits.

Proposed initial worker bounds: 1,000 new requests, 400 MiB received bytes and
3,600 seconds per invocation, whichever is reached first. These are planning
defaults to check against representative response sizes. A checkpoint-aware
controller can continue unfinished work in another bounded invocation throughout
the day. It must not wait a full day merely because a per-run resource bound was
reached. It stops at the shared daily/provider budget or completion, never overlaps
workers, and distinguishes continuation of new work from retry of a failed request.
The current once-daily timer/service must be deliberately adapted during the
activation step; changing DAILY_CAP alone cannot implement this behavior.

The effective budget is the minimum of the configured daily allowance minus all
locally charged/reserved attempts and fresh provider-reported remaining allowance,
within the matching reset window. Coordinate local Equibles work against the same
ledger; another account client can consume provider allowance. Retain quota headers
and observation time. Do not assume FMP or Nasdaq allowances changed.

The upgrade needs explicit state conversion:

- Retain the original 519-member checkpoint, completed calls, response cache,
  partial pages, pending reservations and failure evidence.
- Map progress by stable subject/request identity and source snapshot. Do not
  replace and re-sort the roster under its old numeric cursor.
- Preserve every current-day charged attempt, including failures and the old
  controlled continuation. Upgrading the plan is not a new quota day or refund.
- Record a quota-policy transition. An old saved remaining=0 under the 100-call
  ceiling must not permanently suppress the new entitlement, and an old response
  header must not be treated as fresh remaining allowance under the paid plan.
- Reconcile paid-plan headers in the first explicitly bounded acquisition unit;
  charge that request to the new policy and retain the old ledger.
- Add only missing selected work. Keep an unfinished selected ticker's complete
  available history together before advancing, as previously requested.
- Resolve a pending uncertain request from retained evidence before continuing;
  the plan upgrade does not authorize replaying the failed request.
- Preserve explicit 404 transcript gaps and the existing fail/deferral semantics.
  A 429 defers according to verified quota/reset policy; it is not a busy retry loop.

Version the checkpoint and progress/status contracts together. Show selected
members, resolved subjects, completed histories, missing calls, quota limit,
charged attempts, provider remaining, reset time and bounded-run progress.
The status reader must retain compatibility with old 519/100 receipts.

At one-second minimum spacing, 100,000 calls already require over 27.7 hours
before network and publication overhead. Therefore 100,000/day is capacity,
not a completion estimate. Measure catalogue size, page counts and elapsed time
before estimating the remaining backfill duration.

Raw transcript expansion does not automatically expand model-based transcript
analysis or its separate model-attempt/spend budgets.

## 6. Implementation and rollout order

1. Implement shared universe snapshots, provider/issuer mappings and explicit
   collector bindings. Preserve running scopes until cutover and applied migration
   bytes. Reconcile the stable registry/migration head before allocating ordinals.
2. Implement coverage inventories, retained-response reuse and bounded per-provider
   queues for every collection row. Size aggregate provider consumption and
   distinguish historical backfill, corrections and current maintenance.
3. Adapt the price and SEC workers, including ETF/index retention, price-scope
   compatibility and per-CIK request deduplication. Resolve/publish new issuer
   identities before FMP datasets that require them.
4. Adapt FMP statements, annual/quarterly estimates, dividends/splits and earnings
   dates. Complete the separate endpoint review for other FMP inputs and add only
   explicitly selected units. Preserve existing source-family/read contracts.
5. Adapt Equibles checkpoint/quota/status contracts for full-universe transcripts,
   and news matching/batching for the larger roster. These changes can progress
   independently of the Sharadar source adapter once shared mappings are stable.
6. Implement Sharadar source storage/acquisition, all six SF1 dimensions, observed
   revisions and source-aware common readers under its existing design.
7. Run required offline gates on a stable integrated baseline. Prepare finite
   preflight manifests for all expanded workers with exact subjects, CIKs,
   endpoints, periods, pages, caps, credential resolvers and owning stores.
8. Verify mappings, data coverage, quota headers and representative payloads within
   those manifests. Reuse preflight evidence, backfill missing work, preserve
   earlier facts and record per-dataset coverage/gaps for each selected member.
9. Transition each existing recurring binding deliberately without duplicate
   collectors. Proposed maintenance: existing market-close cadence for daily
   prices; weekday issuer-deduplicated SEC checks; weekday actions/earnings/FMP
   statement checks and annual/quarterly estimate snapshots; existing hourly
   news cadence with revised batching; daily new-transcript catalogue checks
   plus quota-aware backfill continuations. Size distinct FMP-input cadence
   individually; Sharadar follows its table-specific plan. Options/macro retain
   their independent bindings. Freeze each invocation's scope and finite budget.

The source tree contains concurrent uncommitted company/transcript work. Reconcile
those prerequisites rather than replacing them. Keep one integration owner for
registry/schema/time/identity/lock changes. No new provider database is proposed.

## 7. Required acceptance evidence

- CSV membership, immutable source hash, duplicate/alias handling, and unchanged
  existing collector scope until each explicit transition.
- Daily-price union retains every selected ETF/index binding; historical gaps,
  calendars, source/adjustment meaning, existing versions and return-reader
  compatibility are verified beyond the old 800-instrument bound.
- SEC requests deduplicate by CIK without collapsing securities; issuer links,
  filings/accessions and CompanyFacts preserve lineage and time semantics.
- Dividends/splits stay security-specific; earnings dates/corrections and FMP
  estimate-family selection avoid duplicate writers or lost prior records.
- News covers every resolved selected symbol in deterministic valid batches;
  source/article pagination, partial batches, fixed global feeds and aggregate
  request accounting are checked beyond the old 800-symbol roster.
- Other FMP endpoint-review outcomes are explicit; options, macro and independent
  ETF/index scopes have no accidental acquisition expansion.
- State migration with a mid-ticker cursor, completed/out-of-selection members,
  partial pages, cached responses, pending failures and same-day quota upgrade.
- Shared quota accounting across invocations, fresh versus stale headers,
  midnight/reset boundaries, 429 deferral, malformed headers and 100,000 ceiling.
- Complete pagination, empty/missing/unsupported outcomes, no implicit retries,
  finite runtime/bytes and restart without duplicate acquisition/publication.
- Source-specific versioning, AR/MR separation, FMP forecast-period semantics,
  exact semantic no-write replay, cutoff behavior and preserved original evidence.
- Existing price/return/FMP/SEC/news/transcript readers and status compatibility, migrations,
  registry predecessor projections and company-store writer coordination.
- Required full offline suite and fresh independent verification for the shared
  migrations/checkpoint/quota changes, on a stable baseline with stopped writers.
- After authorized activation, bounded request receipts, actual coverage counts,
  raw-hash/lineage checks and completion/gap status for every selected member.

The September 8 documentation-only revision performed no provider calls, credential
reads, canonical-store access, scheduler actions, code changes or executable tests.
Implementation evidence is recorded below rather than retroactively changing that receipt.


## 8. Implementation progress — September 9, 2026

The implementation runs in an isolated candidate with the existing uncommitted
company/transcript prerequisites preserved. Nothing has been committed or pushed.

Step 1 now has an executable candidate: a forward market ordinal 0012,
registry 2.76 with exact 2.75 projection, original CSV BLOB storage, complete
source-symbol membership transitions, provider-specific mapping transitions,
and ten explicit prepared collector bindings in config/collection_bindings.json.
The canonical databases and recurring workers have not yet been changed.

Raw identity content and provider acquisitions are distinct identities. Original
bytes can be equal across providers or capture times. Mapping rows pin their
primary and optional reviewed-association acquisitions. Exact source symbols are
required unless a retained review explicitly links the selected security/share
class to its provider record. Cross-provider instrument links require a matching
FMP association available by the new mapping capture. Unresolved and incomplete
identities remain visible and are ineligible for dependent collection.

Pinned collection selections include membership and provider-mapping versions.
Daily-price and news selections also pin the independent curated_etfs and
major_indexes snapshots, with cutoff checks. No instrument insertion or
membership publication activates a worker. The private collection_universe
operation separates store-free prepare, guarded apply-schema and replay-safe
import-selected commands; only store-free preparation has been exercised outside
temporary fixtures.

Current validation: 29 focused cases passed, including exact source count/hash,
original bytes, source aliases, provider/acquisition separation, no-write replay,
A-to-B-to-A transitions, cutoff and backdating boundaries, incomplete identity,
CIK deduplication, real retained ETF/index snapshots, predecessor upgrade and
rejection, registry projection and generation. A historical option-migration
compatibility assertion was updated to allow later additive migrations; its
actual correction and immutability checks passed separately. Independent review of this foundation has passed. The required full-inventory
gate is still in progress; this is not a completion or live-coverage receipt.

Private evidence is under .local/universe-implementation-20260909, including the
initial baseline hashes, preserved full-suite candidate, store-free preflight,
original test failures and explicit correction rechecks. The full suite continues
on its byte-stable baseline; corrected/new cases run on the successor candidate.
Results must be reconciled by unique current case ID before declaring the gate.

Step 2's private engine is implemented and independently verified with stated
limits. Collection manifests pin provider request identities separately from
membership versions, combine budgets across each provider, and account for
unresolved members. Existing FMP/SEC/price/transcript/news evidence has bounded
immutable inventory adapters. Raw-byte availability and complete HTTP responses
are distinct from complete historical coverage. Missing original evidence blocks
an equivalent historical request rather than silently scheduling a repeat.

The queue accepts exact single-HTTP units, charges and persists attempts before
GET, retains successful or terminal HTTP response evidence before settlement,
and reuses retained responses after publication failures. The real-publisher
crash test proves recovery after canonical commit without another GET or
canonical write. Known 404s remain per-unit failures; HTTP account failures stop
the invocation. A recovered 429 preserves that stop and Retry-After across plans.
Long valid waits are not shortened. Exact microsecond cutoffs, aggregate limits,
deadlines on reuse, and original-day byte accounting have regression evidence.
All 28 focused cases passed in 4.908 seconds. The full-inventory gate remains
pending; original failures, interruptions and explicit rechecks are retained.

Frozen validation now runs in .local/universe-step2-verification, excluding later
draft work. Unique case IDs reconcile completed checks with remaining cases;
duplicate unittest discovery is recorded without counting it as new coverage.
This also keeps source-fingerprint checks independent of ongoing implementation.

The paid-transcript checkpoint conversion is in progress in the active candidate.
It preserves the legacy state bytes, completed/out-of-selection histories,
current partial pages, pending failures and same-day charged attempts. Its
runtime requires an explicit activated binding and leaves legacy checkpoints
under the existing policy. The reported 100,000 daily ceiling is separate from
the proposed 1,000-request/400-MiB/one-hour invocation bounds; live quota headers
have not been checked. This draft is outside the frozen foundation validation.

Remaining work includes source-specific pagination/retained-ledger integration,
provider discovery and new issuer/instrument resolution, price/SEC/FMP/news
worker adoption, completion and review of the Equibles transition, Sharadar
storage/acquisition/readers, finite live preflights/backfill, and deliberate
recurring-binding transitions. These remaining steps include every collection
row in Section 4.

The paid-transcript conversion now passes its bounded independent review.
Thirty-six legacy/paid tests passed in130.095 seconds; eight independent checks
and four midnight-window fixtures passed. Changed-symbol reuse of an existing
instrument is rejected until progress can be explicitly reconciled. Old429
quota evidence preserves its charge without restoring the obsolete100/day stop.
Midnight receipts constrain their verified quota window while the original
request remains charged on its reservation day. Retired partial failures remain
visible when the selected roster is complete. No operational state was converted;
paid headers, activation and the new catalogue-refresh controller remain pending.

The Sharadar source-storage draft adds company migration0012 and registry2.77,
with an exact2.76 predecessor projection. It parses actual response columns and
metadata-declared source keys, retains precise decimals and all unknown fields,
archives original response bytes, and separates six reporting dimensions.
Complete bounded partitions publish immutable evidence, identities, full-row
versions and membership under the company lock. Incomplete cursor walks remain
private. Source definitions and remote snapshot coherence are explicitly
unresolved; no unit conversion or absence-based deletion occurs. Capture-aware
readers distinguish local archive state from provider AR reconstruction.
This later draft is outside the frozen Step1/2 full-inventory snapshot and still
needs its own final verification, provider adapter and common-reader integration.


### Selected collector adapters and later registry candidate

Registry 2.78 is an unapplied additive adapter profile over exact 2.77. It adds
a bounded FMP statement-history collector and a local selected-instrument writer;
it adds no migration. The current candidate has 51 migrations, 70 datasets and
75 collectors. Existing migration resources and generated tool catalogs remain
unchanged. Exact predecessor projection and deterministic generation passed;
the complete integrated regression/review gate is still pending.

The selected price adapter preserves the existing publisher, price representation
and seven-day request-window bound. A 901-equity fixture plus an ETF and index
produced 903 eligible requests. Membership changes do not change an equivalent
selected price request's semantic identity. Older retained evidence must have
a time-valid pinned identity before new publication. A collector rebind
invalidates previously constructed callbacks. The existing scheduled worker and
its request-authority digest remain unchanged; selected sentinel/controller
integration and historical gap manifests are still required.

The private provider queue now has an acquisition-only mode. It retains exact
HTTP responses without claiming canonical publication, supports original-response
reuse with exact request/cutoff validation, and refuses another GET when a known
captured or published request has lost its original bytes. SEC manifests group
all selected source members by exact CIK. Complete retained submissions and
CompanyFacts pairs publish through the existing issuer writer. Partial pairs
remain private, and original response times are retained separately from bundle
availability. The adapter inventories historical submission filenames separately;
it does not yet publish those files or describe them as filing-document text.

Independent review of the price/SEC/acquisition adapters closed four reproduced
recovery/time/lineage blockers. Fourteen adapter checks and sixteen earlier queue
checks passed independently, plus three additional boundary fixtures. Forty-two
combined source/queue checks passed locally in 14.035 seconds. Actual SEC commit
followed by a missing private receipt replayed without a GET or canonical change.

FMP selected manifests now enumerate annual and quarterly statements and
estimates, separate dividends/splits, and earnings observations. Estimates and
earnings use the existing analyst-history source family. Distinct FMP endpoint
families require an explicit endpoint selection; current consensus, dated events
and product segments keep their different meanings. Proposed estimate
continuations are explicit, reparse original evidence, detect repeated pages,
and stop at the finite page cap.

The new statement parser profile accepts at most 1,000 rows and 8 MiB while the
legacy profile retains its 100-row/1-MiB defaults. All unknown payload fields and
original bytes remain available. Reaching a requested limit is explicitly
incomplete; source historical completeness remains unverified even for a shorter
response. The 1,000-row value is a local request ceiling, not a verified live
account entitlement or provider maximum. Public documentation did not establish
the stable endpoint maximum; one credential-free attempt to read FMP's public
api-docs.md returned HTTP 403. No authenticated FMP request was made.

Missing selected instruments can now be inserted atomically with their FMP
mapping under an explicit local-writer mode. New identities require retained
profile evidence explicitly identifying a non-ETF/non-fund equity and use the
existing instrument ID algorithm. Existing instrument records are preserved.
An injected mapping failure rolls back the new instrument and evidence while
the established coordinator retains its failed-attempt receipt. These later
identity/FMP changes are currently under independent review; reported FMP
composite replay and sub-millisecond time boundaries are being corrected.

News has an expanded coverage adapter with separate Alpaca symbols for the
selected equities and independently retained ETFs. Its derived local ETF
manifest carries the original Stage10 snapshot IDs; it is not index-membership
evidence. Missing mappings are coverage gaps, and unsupported indexes are
explicit. The fixture covered 903 matching symbols and 902 Alpaca symbols in
19 complete batches of at most 50. Three new coverage tests and seven existing
refresh tests passed in 4.624 seconds. Active host news bindings select this
coverage before network work; all host bindings remain prepared.

Remaining work still includes discovery/identity preparation and finite live
manifests, complete source-specific continuation controllers, shared provider
budget integration across existing workers, news pagination and aggregate
coverage receipts, Sharadar acquisition/definitions/common readers, Equibles
catalogue refresh, the full stable integrated gate, and deliberate operational
cutovers. No canonical store, provider account, quota checkpoint or recurring
unit has been changed by this implementation.


### Source adapters, exact capture correction and host runtime

The current unapplied registry2.78 candidate now includes company0013, a forward
replacement for the FMP research capture-order trigger. Existing migration bytes
remain unchanged. The candidate has52 migrations,70datasets and75collectors;
registry SHA2563fb931847ebedc3c156ee761ee017678822aa5eed40c569edab70496857cd1f9.
The earlier statement that this cohort added no migration describes the prior
candidate, superseded by the independently reproduced microsecond defect.

FMP publication now recognizes unchanged composite rows across several original
captures. Query ordering and revision comparisons use exact UTC microseconds;
the SQL guard handles the entire accepted offset range, including±23:59.
Estimate continuation rejects repeated or non-extending target dates and pages
whose conflicting rows cannot establish progress. Independent review closed
these findings:30/30 SQL comparison cases, populated2.77→2.78 upgrade preserving
earlier evidence/receipts, exact predecessor projection and52 migration hashes.
The final two edge regressions passed independently in1.626s.

Implemented additional source paths:
- operations/collection_sharadar.py: fixed SF1 metadata plus one explicit
  reporting dimension per ticker batch; finite cursor acquisition through the
  shared private provider ledger; complete-partition publication, retained
  partial-page continuation and no implicit HTTP retry.
- market/collection_identity_preparation.py and the original-array TICKERS
  mapping adapter: exact-symbol evidence preparation, explicit ambiguous and
  unsupported results, and existing-format FMP equity creation. Alias
  reconciliation remains separately evidenced.
- company/fundamental_sources.py: common internal source-aware reads over SEC,
  FMP and SF1, preserving source rows, reporting basis, units and local/source
  availability. No implicit metric equivalence or fallback policy. Source
  symbols are filtered before FMP pagination. Sharadar versions must match
  the permanent provider identity retained with their original capture.
- operations/collection_transport.py: fixed FMP, SEC and Nasdaq routes, existing
  named credential resolvers, one HTTPS request in a bounded child process,
  safe quota headers and credential redaction.
- operations/collection_runtime.py: selected FMP/SEC queue integration and a
  separate price session plan. The AAPL sentinel is acquired/validated before
  another price request is charged. The temporary price fixture's queue must
  be outside its already-bound root so creating directories cannot alter its
  guarded directory-link count.

Independent review reproduced four additional source-integration issues:
future unused identity proof affected ambiguity, ticker reuse misattributed
SF1 rows, expired retained-only SF1 work could publish, and an Alpaca mapping
failure stopped global news callbacks. Corrections and regressions are in place;
18 focused cases passed25.786s. Their independent recheck is pending.

Other actual local results: original identity preparation plus compatibility21
tests passed16.507s; common reader plus FMP11 passed9.481s; transport5 passed1.030s.
Runtime FMP/SEC cases passed in the original3-case run; the price fixture failed
because its queue created a direct child of the guarded fixture root. Moving
only the private queue into a separate/tmp root fixed that case in the explicit
recheck. Original failed logs remain preserved. These results are bounded
evidence, not a completed integrated full gate.

Nasdaq documentation was reread for exact metadata.json paths, comma-separated
filters,10,000-row pages and cursor termination:
https://docs.data.nasdaq.com/docs/parameters-1
https://docs.data.nasdaq.com/docs/in-depth-usage-1
No authenticated provider request or operational publication was performed.

Remaining work includes definition-history storage/collection, complete host
continuation/preflight and activation wiring, Sharadar inventory/watermarks,
explicit SEC historical-index coverage, news aggregate/pagination reporting,
Equibles catalogue refresh/continuation epochs, final stable offline validation,
and integration into the original working tree with concurrent prerequisites
preserved. No scope item has been silently marked complete or activated.


### Definition history and finite estimate continuation

The current unapplied candidate is registry 2.79.0 with 53 migrations,
72 datasets and 76 collectors. Its SHA-256 is
b027cd7cdbe9d1f17293249f8564712d7e703ecfbfb3d7b651848ed5f7a2ae22.
Company 0014 adds separate INDICATORS metadata/capture evidence and SF1
definition versions/membership. Original SF1 facts keep their original
definition-state evidence; the separate definition reader resolves the
definition snapshot available at an explicit local cutoff. A changed snapshot
cannot regress ingestion time. Raw blobs are shared only through the immutable
company-owned artifact table. All earlier migration bytes remain unchanged.

Definition acquisition uses a fixed SF1 table filter, one metadata response,
at most ten data pages, 10,000 rows, 32 MiB and 600 seconds. Partial cursor walks
stay private. Repeated pages, future evidence, deadline expiry and incoherent
metadata stop publication. Exact replay writes nothing; changed definitions
and reversions remain queryable. These are local capture histories, not
reconstructed historical definition vintages.

The original independent source corrections are closed (18 tests, 16.397s).
Runtime/transport corrections are closed (15 tests, 10.004s), including missing
retained AAPL evidence, exact price bounds, partial IPC frames and truncated HTTP
bodies. The definition review found two additional cutoff/deadline failures;
both have regressions and 11 local tests pass in 21.103s. Independent recheck is
pending. Original failed logs remain audit evidence.

The finite FMP estimate controller now walks each annual or quarterly page
sequence with at most ten requests, 10 MiB and 600 seconds. It validates progress
before publishing a page, preserves original capture times, resumes retained
pages without another GET, and stops without hidden retries. Twelve focused
tests passed in 26.047s. A short page does not establish complete historical
estimate vintages; a full final page explicitly reports the local page cap.

Expanded news reports include independent mapping gaps, unsupported indexes,
requested/deferred batches, global feed request counts and a finite aggregate
byte bound. Request-symbol batches and response limits are separate. The current
source profiles acquire only the first page; time-window completeness remains
unproven and full response pages are reported. Eleven compatibility tests
passed in 4.754s, and the added deferred/full-page report regression passed.

The frozen Step 1/2 inventory finished 727 outstanding unique cases in
10,531.394s: 679 passed, 22 failed current registry-count assertions, and
26 errored on the old Inspector registry pin. Together with the prior passes,
it supplies the original inventory evidence; it is not a clean current-head
gate. Current assertions have been corrected to 53/72/76 and Inspector to 2.79.
Final reconciliation, new-case inventory, and stable integrated validation are
still required.

No authenticated provider request, operational migration/import, recurring
change, paid checkpoint conversion or original-workspace integration has
occurred. Remaining work includes the final offline gate and integration,
finite identity/entitlement preflight, canonical coverage inventory, shared FMP
allowance adoption across legacy workers, deliberate worker cutovers, and
Equibles catalogue refresh/later membership conversion. SEC older filing-index
files require an explicit enumerated historical scope; recent submissions are
not labelled full filing history. These pending items are not marked complete.


### Shared request budgets, catalogue continuation and missing price sessions

Independent review has closed the definition-history corrections (11 tests,
20.699 seconds), estimate/news deadline and zero-batch corrections (45 tests,
53.138 seconds), and retained-inventory scaling correction. Inventory filtering
now happens before the bounded result limit: 2,001 unrelated captures do not hide
a selected capture, and repeated metadata associations do not consume distinct
acquisition capacity. The actual selected-capture bound remains enforced.

Prepared FMP account coordination covers selected requests and the current
market-close, company/macro and news transports. Configuration 1.1 keeps the
allowance prepared with a null policy; legacy 1.0 configuration remains readable.
Activation requires an evidenced daily ceiling, maintenance reserve, pacing,
effective date and already-charged count. One shared private account ledger
charges before dispatch, coordinates clients, preserves uncertain attempts and
stops on systemic provider failures. It does not grant additional requests or
alter macro coverage. The source queues retain their independent raw evidence
and publication progress. Actual paid-plan limits remain unverified.

Equibles can prepare a later catalogue epoch without resetting earlier quota,
transcript evidence or known-call identities. Later selection changes preserve
multiple deferred partial tickers, including removal and restoration. Explicit
finite continuations share the original total deadline and retain undispatched
reservations conservatively. A catalogue refresh discovers newly advertised
calls; it does not automatically refetch earlier transcript text or saved 404s.
The corrected selected count includes captures in selected deferred tickers.

Historical price planning uses explicit per-security session calendars and
canonical availability at a cutoff. It retains the independent ETF/index roster
and emits only missing sessions in bounded windows. It does not infer exchange
holidays, truncate a plan at its cap, or count evidence captured after the cutoff.

Actual focused results:
- Shared FMP allowance and transport/binding checks: 24 tests, 14.405 seconds.
- Legacy FMP transport compatibility: 43 tests, 4.801 seconds.
- Binding configuration recheck: 7 tests, 0.124 seconds.
- Price missing-session planner: 3 tests, 3.008 seconds.
- Equibles catalogue/deferred-count correction: 14 tests, 3.363 seconds.

Independent review of this group is pending. Earlier failures and their
correction logs remain preserved. These focused results do not replace the
required final stable full-suite gate. Existing recurring entry-point migration
calls found in current-news preparation target the news store only; no new
market/company migration has been executed.

Remaining operational work requires concrete finite preflight manifests,
original provider identity evidence, current canonical coverage inventory,
verified account policy, explicit market/company migration and import, and
deliberate worker/checkpoint cutover. All ten bindings remain prepared.
No provider request, credential read, operational write, recurring-unit change,
or original-working-tree integration has been performed.


### Integration candidate and first operational proposal

Independent review closed the prepared shared FMP allowance, paid count and
missing-session planner: 31 focused tests passed in 8.068 seconds. An independent
five-transport probe shared one temporary ledger, charged six requests, enforced
maintenance capacity and spacing, and retained no synthetic credential in its
receipts. This verifies the inactive implementation, not an account entitlement
or live schedule.

Transcript pipe reads now keep partial headers/bodies inside the fixed request
deadline. SF1 publication checks the invocation deadline after revalidation,
compression and physical locking and before canonical work. Expiry leaves
retained evidence resumable without another GET. The next review reproduced a
backdated catalogue epoch when a removed ticker still held a captured call.
The correction checks the selection transition and every original page time
supporting carried deferred calls. Fifteen catalogue tests passed in 3.893
seconds; independent chronology closure is pending.

The original workspace acquired concurrent Status-page and transcript-analysis
changes during this implementation. Thirty-three source/document/test files
were retained as integration prerequisites. The two shared Inspector files were
merged by preserving that work and changing only the current registry pin and
matching assertion to 2.79.0. Review caught that the earlier candidate's actual
Inspector pin was still 2.77.0 despite the prior progress note saying 2.79.0;
the code is now corrected. The generated-output check passes. No applied
migration or historical assertion was rewritten.

[Bootstrap preflight proposal](UNIVERSE_BOOTSTRAP_PREFLIGHT_2026-09-09.json)
records all 2,248 source symbols and nine proposed acquire-only requests:
three FMP profiles (GOOG, GOOGL, MSFT), three Nasdaq metadata responses
(TICKERS, SF1, INDICATORS), one SF1-filtered TICKERS response for those three
symbols, one SF1-filtered INDICATORS response, and one MSFT Equibles catalogue
page. Bounds are nine total attempts, 33 MiB of response bytes, 600 seconds,
one-second spacing and no retries or automatic cursor continuation. Existing
named credential resolvers are listed without credential values.

This is a proposal, not an executed or authorized manifest. The original
retained-request inventory must first resolve reuse and explicitly approved
overlapping fresh observations. It does not repeat the completed AAPL SF1/ARQ
dry run. Further exact worker manifests depend on evidenced provider IDs/CIKs,
existing coverage, calendars, original transcript progress and verified account
policy. No unknown ID, historical range or entitlement is filled in by assumption.

Official public references used for the bootstrap:
- [FMP company profile](https://site.financialmodelingprep.com/developer/docs/stable/profile-symbol)
- [Nasdaq table parameters](https://docs.data.nasdaq.com/docs/parameters-1)
- [Equibles earnings-call endpoints](https://equibles.com/docs/api/endpoints/earnings)

The fixed candidate's full offline gate and final integration review are next.
Canonical migrations/imports, paid checkpoint conversion, live acquisitions and
recurring cutovers remain unexecuted.


### Final publication deadline correction and verification baseline

Independent review closed the deferred-catalogue chronology correction.
The first final full-suite attempt was deliberately interrupted after 44 passing
test occurrences when review reproduced a runtime composition defect. Its
frozen source and original log remain unchanged; that interrupted run is not a
passing gate.

The selected runtime now carries its original absolute invocation deadline
through parsing, source revalidation, physical locking and canonical
publication. Price backfill and sentinel publication use the same outer
deadline. A dedicated internal PublicationDeferred exception rolls back an
expired publication without recording a permanent failed ingestion identity.
Ordinary resource failures retain their existing failure receipts. Retained
response bytes, capture times and charged requests survive deferral, permitting
the same work to continue without another provider GET.

Seventeen deadline regressions passed in 17.804 seconds; 53 adjacent controller
and source checks passed in 46.136 seconds; the five existing coordinator
atomicity checks passed in 3.992 seconds. Independent writer-boundary probes
closed the remaining failure for statements, dividends, analyst estimates and
Sharadar SF1. They confirmed unchanged canonical fingerprints on deferral,
zero-GET continuation, exact replay and preserved genuine failure recording.

A new immutable final verification baseline supersedes the interrupted
candidate for the required full offline gate. The original workspace remains
unmodified by this implementation, and its recorded concurrent prerequisites
match the candidate. No live provider population, canonical migration/import,
paid checkpoint conversion or recurring adoption has occurred. All ten
collection bindings and the account policy remain prepared.


### Completed source integration and offline validation — 2026-09-09

The reviewed 120-path implementation is now integrated into the original
working tree. All 854 files in the effective validation baseline matched after
application; two new test files required restoration of their reviewed line
endings. The original branch, HEAD and index were preserved, together with
the recorded concurrent prerequisites. No commit or push was performed.

The implementation provides the separate versioned 2,248-member selection,
provider-specific mapping and collection plans, bounded resumable collectors,
and separate Sharadar evidence, native facts, revisions and definitions.
All ten collection bindings are prepared. Daily prices retain the existing
ETF/index selections; options, macro and transcript model analysis retain
their independent scopes. Other distinct FMP endpoints require explicit
endpoint selection under the prepared review policy.

The required full offline command completed on the immutable final baseline:
2,033 test occurrences in 15,801.850 seconds, with 2,032 passes and one error.
The original exit status was 1 and its log remains unchanged. The sole error
was a historical transcript-activation test using the current registry instead
of its exact historical profile. A reviewed test-only correction restored the
historical profile and added a rejection assertion for the newer registry;
the affected test and ready-ledger guard passed both local and independent
rechecks. Production activation code was unchanged by that correction.

Four imported duplicate test IDs were explicitly reconciled, yielding passing
evidence for all 2,029 unique cases in the full run. Six later concurrent
prerequisite files were preserved outside the patch. Their combined offline
compatibility run passed all 43 cases in 50.325 seconds, adding 14 unique cases.
The effective integrated baseline therefore has passing evidence for 2,043
unique cases, with no skipped or uncovered cases. Independent final review
accepted this completed full gate with a reviewed correction; it was not an
uninterrupted clean full-suite run.

Running the integrated store-free command
`python3 -m quant_data.operations.collection_universe prepare` confirmed
2,248 selected members, all ten bindings prepared, zero provider requests and
zero canonical writes. The exact CSV SHA-256 remains
`9393c72160eae41fc3af10a16af4ecc5ab2ab8f8bb7a5d4130162fa9e4aff15a`.
Provider mappings remain unchecked against live evidence.

Evidence is retained under `.local/universe-implementation-20260909/`:
`final-integration-receipt.json`, `final-effective-integration-baseline.json`,
`final-v2-full-suite-reconciliation.json`,
`final-late-prerequisite-compatibility.log`, and
`final-main-universe-prepare.json`. Frozen verification trees and original
failed/interrupted logs remain unchanged.

Live rollout remains pending. This implementation task performed no
authenticated provider request, credential read, canonical-store access or
write, schema migration/import, paid checkpoint conversion, or recurring-unit
change. The separate concurrent transcript pilot retains its own authority
and evidence; it is not an operation performed by this task.

The next proposed live step is the linked nine-request acquire-only bootstrap,
bounded to 33 MiB, 600 seconds and no retries or automatic cursor continuation.
It remains unapproved and unexecuted. Subsequent mapping completion, explicit
market/company schema and membership application, finite backfill manifests,
verified account policy and deliberate recurring adoption remain operational
steps; prepared bindings alone do not activate them.


### Approved bootstrap execution — 2026-09-09 UTC

The user explicitly approved the prepared nine-request, 33 MiB, 600-second
acquire-only bootstrap with no retries. The exact proposal bytes remain
unchanged at SHA-256
39354334d291628de0f4030cdbc0df12ea8e49ffaf3add155b3f4f0a8a41893f.

Execution on September 9 at 16:01 UTC reserved eight single attempts: three
FMP profiles, three Nasdaq metadata requests, one Nasdaq TICKERS page, and one
MSFT Equibles catalogue page. Seven HTTP 200 responses were retained, totaling
28,642 bytes. The acquisition invocation took 7.960 seconds. The TICKERS
transport returned no usable result; its HTTP status, payload and root cause
are unknown. Its attempt remains charged and cannot be silently retried.
That source-local stop left the INDICATORS data request unattempted. No
redirect, retry, alternate endpoint or cursor continuation was requested.

The three FMP profiles validated GOOG, GOOGL and MSFT as active equities.
GOOG and GOOGL both reported CIK 0001652044; MSFT reported CIK 0000789019.
Nasdaq returned TICKERS metadata with 28 columns, SF1 metadata with 112 columns,
and INDICATORS metadata with seven columns. This establishes these metadata
responses, not successful SF1 fact acquisition or full selected-universe
coverage. Representative Nasdaq security mappings and the definitions data
page remain unavailable from this invocation.

The MSFT Equibles catalogue returned 27 earnings-call entries, all advertising
transcripts, covering fiscal 2020 Q2 through 2026 Q4 with no next page.
The response body exactly matches the earlier retained catalogue, while the
new capture and quota headers remain separate evidence. Headers reported a
limit of **10,000**, remaining **9,900**, and reset **2026-09-10 00:00 UTC**.
This differs from the user-reported 100,000-call plan. Do not treat 100,000 as
verified account capacity; reconcile this discrepancy before paid activation.
The bootstrap did not convert or modify the existing quota checkpoint.

Actual Nasdaq metadata uses lowercase text and double declarations. Both
native parsers initially rejected the lowercase identity types. A narrow
provider-parser correction now accepts those observed spellings, preserves
the original metadata/type declarations, and converts double values through
the existing exact decimal path. Existing fixture schema, key and semantic
identities remain unchanged. Four new regressions and all adjacent Sharadar
parser/repository/controller/inventory checks passed: **47 tests in 29.567
seconds**. Both real retained metadata responses now pass their native parsers
without any additional GET. The original execution receipt retains the
pre-correction analysis; the separate revalidation receipt records the fix.
No registry, migration, shared identity/time contract or applied schema changed;
the focused provider-compatibility checks were used instead of repeating the
previously completed full integration suite.

Private raw bytes, durable reservations, authorization and the original result:
data/.operations/collection-preflight/39354334d291628de0f4030cdbc0df12ea8e49ffaf3add155b3f4f0a8a41893f/.
Local correction evidence:
.local/universe-implementation-20260909/bootstrap-offline-revalidation.json,
bootstrap-parser-precorrection.json, and bootstrap-parser-focused-tests.log.

No canonical store was opened or written. No schema/import, scheduler,
recurring unit, quota policy, commit or push was performed. The authorization
was confined to this invocation; no unattempted unit or uncertain attempt adds
retry authority. Live mapping completion, definitions acquisition and the
larger backfill remain pending.


## Authorized operational rollout — September 9, 2026

The user has now approved full selected-universe storage activation, missing
history backfill and live-fetcher rollout. The earlier prepared-only state is
historical. Reviewed migrations and the 2,248-member selection are applied;
Sharadar/SEC mappings and bindings are active. Sharadar has 533 of 534 history
partitions published; its single failed ARQ batch awaits the bounded recovery
decision. SEC missing-issuer backfill has processed all 1,711 planned pairs,
adding 1,669 successful issuers and preserving 42 explicit issuer gaps.
Including existing coverage, SEC represents 2,204 selected securities across
2,182 issuers; NBN and TOWN remain identity gaps. Its expanded weekday refresh
is enabled, and the reviewed durable conflict holds are installed. Live Fetcher
is restored and its status route returns HTTP 200. FMP's evidenced account
allowance and the full Equibles mapping/checkpoint transition remain pending;
the complete multi-provider backfill is not yet achieved.

The [rollout receipt](SELECTED_UNIVERSE_ROLLOUT_2026-09-09.md) owns current bounded
operations, actual validation, source coverage gaps, and schedule activation
evidence. It must not be read as complete multi-provider backfill until those
outcomes are recorded.
