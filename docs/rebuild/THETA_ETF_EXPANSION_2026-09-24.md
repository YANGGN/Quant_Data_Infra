# Tier-1 ETF options expansion - 2026-09-24

Status: Completed with gaps; main and final recovery passes finished at 17:09 Toronto on September 25, 2026. 35,167 of 37,125 ETF sessions saved; 1,958 gaps remain.

The user requested expanding the completed SPY history to other major ETFs,
using concurrent download workers. The existing Tier-1 list supplies the scope:
QQQ, IWM, DIA, XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY.
SPY's completed 2,696-day population is excluded from all new requests.

The frozen manifest contains 37,125 symbol/session units, newest date first and
then the declared ETF order. Range: 2016-01-04 through 2026-09-23, except XLC
starts at its issuer-listed 2018-06-19 listing date. Fund inception was the
previous day; this is not a claim that options existed on every listing day.
Source: https://www.ssga.com/us/en/individual/etfs/state-street-communication-services-select-sector-spdr-etf-xlc

## Finite operation and storage

One global four-worker download pool and one coordinator/writer. Allocation:
120,000 data requests, 256 GiB received, 72 hours from the first run start,
zero retries, 25 GiB options database ceiling and 20 GiB free-space reserve.
The theoretical three-request-per-unit bound is 111,375. No subscription change,
purchase, terminal, recurring job, legacy-store mutation or SPY refetch.

The same compact policy retains up to 300 selected contracts, 102 population
summary cells and 40 minimal leader references per ETF/session. Full sources
are removed only after committed publication and immutable verification;
failed/partial units retain cached material. Digests do not reconstruct discarded
sources. Exact roots retain unverified-deliverable and provider-vintage labels.

Calendars are reused from the verified SPY collection with original metadata and
copy digests. Local price references use market.get_price_series@2.0.0 from
runtime registry 2.92.0; manifest/describe and complete responses are retained.
Most symbols have 2,696 reference days; XLC has 2,077. IWM's retained references
start at 2021-08-30 (1,272 days). Greek responses supply their own historical
underlying references; earlier price-only days require a compatible local
reference. Missing or split-incompatible references yield explicit gaps and
retained inputs. No modern adjusted close is silently relabelled as a raw price,
no Greek is fabricated, and no additional equity provider fetch is introduced.

## Schema and compatibility

Companion registry config/options_registry.json advances to 1.1.0. New migration
options:0002_tier1_etf_roots changes only the root CHECK restrictions on
option_captures and option_contracts, copying all existing values verbatim in an
atomic transaction and recreating their indexes/immutability triggers. Existing
child tables, IDs, SPY captures, metadata and old migration bytes remain intact.
Migration 0001 SHA-256 remains
63691b621c0a4ea0b5e03beb32028dfeea646260be5147c067b287fa914ddbff.
Migration 0002 SHA-256:
ae6a5b7b81429b720cd5cfef4c6e65722e0921f785bd88dee3e02ab60d8d1e2d.

The reader accepts validated migration prefixes so the existing SPY backup
remains readable/restorable; new writes require the complete chain. Exact SPY
semantic replay remains unchanged. Model and publisher reject cross-symbol
details. Request scope excludes SPY for the new collector.

Publication continues to enable SQLite's immediate foreign-key enforcement.
The previous whole-database foreign_key_check on every daily insert is removed
to prevent quadratic backfill work; full checks remain at migration, health,
completion and backup. A regression proves an invalid predecessor rolls back
publication.

Implementation: options/{universe,model,store,transport,job,reference,etf_job}.py,
operations/theta_etf_backfill.py, optional registry and migration 0002. Existing
public four-store contracts and Alpaca tools are unchanged.

## Validation and operational evidence

Changed boundary: options-only schema migration, multi-symbol isolation, finite
batch recovery and the existing serial publication consumer. Required checks:
tests.options.test_theta_compact, tests.options.test_theta_parallel,
tests.options.test_theta_etf; 44 tests passed, no skips. Tests cover every-SPY-fact
preservation, transactional migration rollback, migration replay, symbol-safe
keys/references, unchanged SPY semantic fingerprints, one global concurrent pool,
duplicate/retry prevention, cleanup, backup and immediate foreign keys. All store
tests use explicit temporary roots. No shared-core invariant or generator changed,
so no unrelated full-suite gate applies. Independent verification precedes
canonical migration and live acquisition.

Private run state: .local/theta-etf-20260924/{manifest,status,attempts,
request-receipts,reference-preparation}.json or .jsonl as appropriate.
Dated migration, launch and first-publication evidence will be appended below.

## Activation evidence

Independent verifier /root/theta_options_verify reran the same 44 tests (all
passed, no skips, 6.748 seconds) and separately checked predecessor chains,
legacy backup read/restore/upgrade, complete SPY fixture equivalence, and
symbol/digest-bound cleanup. No blocking findings; source hashes are retained
in .local/theta-etf-20260924/independent-verification.json.

Migration completed at 2026-09-24T23:05:52.593192+00:00. Under the physical store
lock, every row in all seven fact/current tables was deterministically hashed
before and after, and against the existing verified SPY backup. Every hash and
count matched. Both migration entries validate; quick_check is ok and foreign
key violations are zero. The 2,696 SPY sessions, 666,581 details, 274,992 summary
rows and 213,276 selected contracts remain unchanged. Evidence is retained in
migration-verification.json. Database after migration: 773332992 bytes.

The fixed manual operation was launched at 2026-09-24T23:06:07.806601+00:00 with PID
329558. Four global downloads share one finite allocation; the
serial coordinator alone publishes. Source fingerprints were rechecked at
launch. launch.json, status.json, attempts.jsonl and request-receipts.jsonl
retain operational evidence. Live progress is not a completion claim.

## Early live verification

At 2026-09-24T23:07:10.801782+00:00, 34 of 37,125 ETF/session units were
committed with zero recorded gaps. All 14 ETFs had the first requested day
(2026-09-23) saved. Direct immutable checks confirmed per-capture retention
limits, all 102 summary cells, root-correct contracts/source receipts, and
deletion of each published first-day source cache. Receipt timings demonstrate
four overlapping provider requests. No duplicate attempt IDs and no SPY
requests were present; SPY remains at 2,696 sessions. Process 329558 was
running. Database size: 773332992 bytes (migration freed pages are being
reused). This is an early snapshot, not a full-history completion or ETA claim.
Full evidence: .local/theta-etf-20260924/early-live-verification.json.

Historical price-only coverage remains subject to the explicit missing-reference
and split-basis gaps described above. The operation stops at its original finite
limits; restarting does not reset allocation or permit retries.

## September 25, 2026 - bounded ETF retry and final gap recovery

The user explicitly approved retrying the interrupted work and requested one
retry for missing data, logging and continued progress after that retry, then
one final pass over the missing data after the main population is processed.
This newer decision supersedes zero retries for this ETF expansion only.

The same 14 roots and 37,125 symbol/session manifest remain fixed, with SPY and
all completed ETF captures excluded. Per endpoint: at most two total main-pass
attempts (original plus one retry), then at most one additional attempt during
one final gap pass. Existing September 24 attempts count toward these limits.
Successful staged responses are reused; completed populations are never replayed.

No allocation resets: 120,000 requests, 256 GiB received, and the original
72-hour clock starting 2026-09-24T23:06:10.013047+00:00 remain in force, including
retry and recovery requests. Four downloads globally, one writer, 25 GiB store
ceiling and 20 GiB disk reserve remain. Credentials use the established project
THETA_DATA_API resolver, provider is Theta, and the target is data/options.sqlite.
No subscription, scheduler, migration, or other provider/store change.

The frozen original manifest remains dated evidence. retry-policy.json records
the additive policy bound to its digest. attempts.jsonl retains each attempt
number and phase; request-receipts.jsonl retains every response/error receipt.
gap-events.jsonl appends missing/resolved events; status.json gives outstanding
gaps and phase. Restarting does not reset attempt limits or grant another pass.

Retryable provider codes: UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED,
INTERNAL, ABORTED, CANCELLED. Empty/no-data responses receive the same one retry.
Retry delay is jittered between one and two seconds outside store locks.
Exhausted transient errors, absent price history or absent open interest are
logged as day gaps and do not stop other days. Authentication, permission,
unknown schema/contract failures, storage failures and exhausted global bounds
still stop the operation. Partial failed streams are never published.

A valid older price-only fallback remains valid when historical Greeks are
unavailable; it is explicitly labelled, not fabricated. Missing local reference
or incompatible split basis remains a quality gap. The final pass rechecks
those retained inputs locally; it does not refetch successful inputs or silently
change their price basis. An unresolved gap after recovery remains visible, and
completion with gaps exits nonzero. Unneeded fallback sources are included in
verified same-day cleanup after successful publication; unresolved sources remain.

Changed component: ETF request recovery/coordinator and its private registry
policy. Shared transport, physical locking, model, canonical publisher, schemas,
and completed SPY behavior are unchanged. Checks: tests.options.test_theta_compact,
tests.options.test_theta_parallel, tests.options.test_theta_etf, and new
tests.options.test_theta_retry. Primary result: 56 passed, zero skips, 7.633s.
The first test run found a redundant staged EOD fallback after successful Greek
recovery; its exact same-day artifact now joins verified cleanup and the full
focused set passes. Independent review precedes resumed publication.

Independent review found a restart accounting gap: an attempted stream could
consume bytes and crash before a terminal receipt, making a retry undercount
received bytes. The corrected request loader requires terminal receipt counts
to match durable attempt counts per request identity before authentication.
An unreceipted attempt stops with request_receipt_accounting_mismatch; no byte
allocation is silently reset. Both original UNAVAILABLE attempts have complete
receipts and remain eligible for their single main-pass retry. A regression
consumes 400 bytes under a 500-byte cap and crashes mid-stream, then proves
resume stops before authentication without erasing that evidence. Final primary
focused result: 57 passed, zero skips, 7.705 seconds.

Retry activation 2026-09-25T13:18:27.470876+00:00: independent review passed after the accounting
correction (57 tests, no skips, 7.699 seconds). The exact stopped checkpoint
passed immutable read-only checks: 10,499 ETF sessions plus 2,696 SPY sessions,
21,002 attempts matched by 21,002 terminal receipts, two successful staged
responses retained, no pending cleanup. Existing capture identities through
ID 13,195 were hashed for post-resume comparison.

The fixed manual entry point resumed as PID 416018, preserving the original
authorization clock and all cumulative counters. The reviewed source fingerprints,
launch and preflight are retained in .local/theta-etf-20260924/retry-*.json.
This dated launch record does not claim completion.

Live retry verification 2026-09-25T13:19:48.350860+00:00: both original UNAVAILABLE requests
succeeded on exactly their second total/main attempt: XLY open interest for
2023-09-27 and IWM EOD Greeks for 2023-09-26. The two successful pre-existing
Greek caches were used without refetching and removed only after verified
publication. Each recovered capture has 102 summaries and bounded selected
details. Existing capture identities through ID 13,195 matched the preflight
digest exactly; SPY remains excluded from requests and has 2,696 sessions.

At this snapshot 10556 ETF sessions were saved (57 since
restart), two retries were used, and there were zero outstanding gaps. PID
416018 was running. The original authorization clock remained unchanged.
Database: 3224993792 bytes. Evidence: retry-live-verification-20260925.json.
This is early recovery evidence, not completion of the historical population.

## September 25, 2026 - completed with explicit gaps

The main pass and one final gap pass completed at 2026-09-25T21:09:36.923331+00:00
(17:09 Toronto). All 37,125 planned ETF/session units were processed; 35,167
were saved and 1,958 remain unresolved after their final pass. 1,953 gaps are
in 2016; the other five are IWM 2019-02-06 and XLC 2018-06-19 through 2018-06-22.
Reasons: 1,623 unverified fallback references, 252 missing contemporaneous
references, 3 incompatible reference bases, 72 absent OI responses, 6 absent
EOD responses and 2 connection failures. No gap was silently filled.

Including SPY, options.sqlite holds 37,863 sessions, 7,705,012 selected rows
and 3,862,026 summary cells. Database and completion backup are each
8,671,899,648 bytes. The job reports quick_check ok, zero foreign-key violations
and backup integrity ok. Fresh immutable main/backup population checks match;
SPY remains 2,696 sessions. All 82,417 attempt records match terminal receipts,
phase retry bounds hold, and no SPY requests occurred. Received bytes total
61,175,808,359. Failed-source caches retained for gaps total 101,103,188 bytes;
there is no pending cleanup. completion-verification.json records evidence.

This finite operation has exited. Completed populations must not be repeated;
unresolved gaps have exhausted the authorized final pass and require a new
explicit finite scope before additional provider attempts. No recurring unit
or automatic repeated gap pass was introduced.
