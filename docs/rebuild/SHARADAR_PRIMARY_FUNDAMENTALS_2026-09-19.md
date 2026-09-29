# Sharadar primary fundamentals and filing-triggered SEC facts

Decision: 2026-09-19. The user approved making Sharadar the routine fundamentals
source while keeping SEC filing metadata and using SEC CompanyFacts for new
financial filings or explicit discrepancy/gap checks.

## Consumer contract

Registry 2.87.0 / versioned catalog 2.33.0 adds explicit 3.0.0 versions of
`company.get_fundamentals` and `company.get_share_count_history`.
Routine consumers select these versions with one selected-universe `symbol`.
The default dimension is ARQ; ARY, ART, MRQ, MRY and MRT are explicit alternatives.
No dimensions are combined, calendarized or converted.

Fundamentals return the complete retained source-native values (including vendor
ratios) in `values_json`, with `missingness_json`, source key, schema ID,
permanent provider subject, capture ID and canonical version ID. The share tool
selects `sharesbas`, `shareswa`, and `shareswadil`. Their definitions remain
Sharadar definitions, without asserting equivalence to SEC mapped metrics or
inventing adjustments. Missing Sharadar coverage is explicit; no SEC/FMP fallback.

`as_of` is a timezone-aware local knowledge cutoff applied to both membership/
identity pins and retained Sharadar versions. It does not reconstruct data before
local capture or prove historical first-release completeness. Source datekey
retains date-only precision. Source dates are never manufactured SEC accession links.
Physical market/company locks protect immutable reads; tools have no network path.

Frozen omitted-version/v1 behavior and explicit SEC fundamentals v2.0, ratios
v2.1 and shares v2.0 remain byte-compatible. Clients explicitly select v3 for the
new routine source. Historical registry 2.86 and older projections remain exact.
No migration, dataset ownership, or applied migration bytes change.

## Scheduled SEC policy

The existing `quant-data-sec-company-fundamentals.timer` keeps its weekday 07:15
America/Toronto cadence and selected binding. Its existing Python entry point
now dispatches the v2 conditional worker. No unit is started, restarted, enabled,
disabled or rescheduled by this change.

Each normal clock observation has at most one submissions GET per unique selected
CIK and at most one CompanyFacts GET when recent submissions contain a financial
accession not already present in this issuer's retained filing memberships.
Financial forms are 10-K, 10-Q, 20-F, 40-F, 8-K, 6-K and their /A amendments.
An issuer without retained memberships treats its financial accessions as unseen.
This is not a full filing-document downloader or historical-index backfill.

Submissions publish separately to existing evidence, issuer, filing and membership
datasets; metadata-only publication cannot add CompanyFacts artifacts or facts.
The trigger decision is retained before metadata publication, so a same-observation
continuation can finish its remaining facts GET without losing the trigger.
Fully retained pairs still use the existing SEC fact parser/publisher and its
original response timestamps. Old facts are never relabelled as freshly captured.

Limits remain <=4,500 requests, 12 GiB responses and six hours per invocation, with
one request/second and no retries. Acquisition reserves before GET. HTTP failures
are retained, uncertain attempts block replay, completed observations return zero
new requests, and bounded continuations only consume untouched units. Source-local
invalid responses and classified immutable metadata conflicts remain issuer-local;
store/lock errors stop publication rather than being hidden.

State is stored in `data/.operations/collection/sec/filing-triggered/current/<date>`.
Legacy `current/<date>` ledgers and SEC canonical history are preserved.
New daily receipts report skipped facts and triggered/requested facts separately.
Failed facts do not automatically retry on a later day solely because values
remain missing; their original failure evidence remains and an explicit check
can address the gap. Normal later filings may trigger a new observation.

## Explicit targeted checks

`python3 -m quant_data.operations.sec_targeted_check --cik <ten-digit-CIK>
--check-id <stable-id> --reason <discrepancy-or-gap>` selects up to ten CIKs from
the active binding, at most two GETs per CIK, 128 MiB/CIK, <=600 seconds/CIK and
<=7,200 seconds total. It requires a separately authorized finite check; none was
executed during implementation. Same check ID/scope reuses retained results even
on a later day; changed scope under that ID is rejected. Completed targeted
invocations are terminal even when a time/request budget leaves a partial result;
only normal scheduled dates can continue untouched work under a new invocation. It never runs on reads
or on the recurring timer and does not overwrite the normal timer's status report.

## Validation and rollout evidence

Impact: company source selection/public version routing, SEC acquisition decisions,
company filing publication, and exact registry predecessor projections.
Required checks: the new isolated policy/boundary tests; existing SEC queue,
deadline, publisher and reader tests; Sharadar repository/source tests; catalog,
local CLI, historical registry projections and generated-artifact determinism.
No live provider check, population repeat, or manual recurring-unit run is part
of validation. The existing service loads the new worker on its next normal clock
execution. Current source-data gaps are not claimed fixed by this policy change.


### Completed evidence (September 19)

- 127 scoped regression tests passed in 577.407 seconds: conditional/legacy SEC
  workers, SEC retained pairs and deadlines, SEC publisher, Sharadar repository
  and source separation, new public readers, existing SEC facts/ratios/shares/
  filings, catalog/dispatcher and transcript compatibility.
- After the independent review corrections, 16 SEC tests passed in 19.487
  seconds, the targeted total-deadline test passed, and the final targeted-budget
  replay plus normal-continuation pair passed in 2.410 seconds.
- Fresh independent verifier (configured Astra xhigh) found no remaining
  blockers after correcting semantic hold evidence, exact exception typing and
  targeted duration/replay handling. Its final five affected tests passed in
  6.742 seconds; independent same-ID replay probe issued zero additional GETs.
- Generated-output check and scoped diff checks passed. Exact registry 2.86
  projection, old default tools, frozen v1 schema, migration declarations,
  collector declarations and registered job declarations are unchanged.
- Supported local manifest/list/describe calls succeeded. Read-only AAPL
  fundamentals and share-count v3 calls returned Sharadar ARQ, permanent subject
  199059, report period 2026-06-27, with original capture/version metadata.
  The first cold fundamentals call exceeded the existing five-second host
  deadline (7.50 seconds); the recheck succeeded in 0.144 seconds and the share
  read in 0.110 seconds. The existing repository query scans observations;
  cold-read latency remains a limitation, not a claim of consistently fast reads.
- Installed SEC timer was active/enabled; its existing service entry point
  remained unchanged. Next regular execution was reported as Monday,
  September 21, 2026 at 07:15 EDT. No provider call or unit start/restart was used
  to validate this change. The first scheduled execution under this policy has
  not yet been observed.

Private receipts, test logs, scoped preimages and changed-file hashes are under
`.local/sharadar-primary-20260919/`. The full repository suite was not run:
this follows the explicit-version/component-policy lane, with unchanged core
schema, identity, time, locking and access semantics and exact predecessor
compatibility evidence. Unrelated preexisting workspace changes were preserved.
