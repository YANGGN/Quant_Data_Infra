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
Missing/malformed quota headers, authentication errors, transport failures,
unexpected transcript 404s, and publication failures pause the backfill for
reconciliation. Successful responses are immutable, content-addressed, and reusable.
No successful request is repeated to recover a checkpoint or publication.

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
