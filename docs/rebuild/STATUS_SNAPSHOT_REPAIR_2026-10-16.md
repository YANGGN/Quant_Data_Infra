# Stored-data snapshot timeout repair — October 16, 2026

The user requested investigation and repair of the local Inspector's stored-data
snapshot timeout. The bounded repair uses retained data only: nine additive
lookup indexes in the existing market, company and news stores, the existing migration
runner, and the existing loopback Inspector. It grants no provider request,
collection retry, population repeat, recurring-unit change or external exposure.

## Diagnosis and correction

The five-second `data.get_dataset_status` 1.0.0 deadline was exceeded by metadata
queries that joined and sorted capture histories to return their newest row.
The diagnostic profile was interrupted after 61.257 seconds: 71 control-plane
lookups had consumed 53.033 seconds, and the first news capture lookup another
8.038 seconds. A query-only candidate still exceeded the deadline.

Registry 2.99.0 adds only these forward migrations:

| Migration | Indexes |
| --- | --- |
| `company:0020_status_capture_lookup` | `ingestion_snapshots_dataset_capture` on dataset, descending capture time and snapshot ID |
| `news:0011_status_evidence_lookup` | `fmp_stock_latest_current_captures_time` on descending capture time and ID; `current_multi_source_attempts_feed` on feed and attempt ID |

The FMP latest-capture query keeps captures as the outer join loop so SQLite can
use the time index and stop at the latest successful capture. Its join predicates,
successful-outcome requirement, binary ID tie-break, ordering and limit are
unchanged. The generic latest-outcome query selects the newest successful run
and newest failed/partial run separately before applying the original final
timestamp and binary-ID ordering. This allows the existing completion index to
stop early instead of loading every successful historical run. Existing migrations and facts,
public tool versions, schemas, missingness rules and the five-second deadline
are preserved. The Inspector pins the new registry version.

The exact predecessor projection returns registry 2.98.0 byte-for-byte. Both
tool catalogs are unchanged. Registry 2.99.0 SHA-256:
`dadbdd6fa05eb882aec48b0709c49f4fd46df8c8e8f3bd2a303785c22c3c20b8`.

## Validation

Impact: company/news metadata lookup plans, forward migration and replay,
retained dataset/source status readers, and local status routing.

- Three isolated `tests.test_status_lookup_indexes` checks passed: populated
  rows/triggers/prior ledger preservation, fingerprint-stable replay, exact
  registry increment, selection and query plans for all three indexes.
- Thirty existing checks passed across `tests.test_data_status`,
  `tests.news.test_tool_repository`, the four focused Inspector data-status
  route tests, and `tests.dashboard.test_unified_status_page`.
- After registry publication, the four focused Inspector route tests passed
  again and generated artifacts checked byte-identical.
- A fresh independent verifier reran the three fixture checks and independently
  checked the exact predecessor, unchanged catalogs, published candidate,
  migration runner, join semantics and Inspector pin. No remaining blocker.

All offline fixtures used temporary stores. The full suite was not required:
no shared validator/generator algorithm or cross-domain invariant changed.
The pre-existing calendar/focus CSS edit is unrelated and preserved separately.

## Local activation evidence

The immutable preflight found exactly company migrations 1–19 and news 1–10,
so only the two declared successors were pending. The validated registry was
published before application so new readers/normal scheduled jobs recognize the
new ledger entries. Application uses `migrate_store`, with its existing physical
store lock, checksum reconciliation, atomic transactions and foreign-key gate.

Detailed pre/post evidence is retained in `.local/status-lookup-activation.json`.
Live timing and browser acceptance are recorded after application completes.

### October 17 resumption

After the user confirmed Ubuntu responded, no original migration process
remained. The company store had a nonempty crash-left WAL, so the immutable
gateway correctly refused access. One company-only open/close through the
existing `StoreWriteLock` and `writer_connection` recovered it; no sidecar
deletion, forced checkpoint, DDL or data mutation was requested by that recovery
step. The immutable reader then succeeded.

The company ledger remained at 0019 with no new lookup index. The news ledger
already contained 0011 (recorded application time `2026-10-17T03:10:00.747561Z`)
and both expected indexes. The completed news migration was verified and skipped.
The remaining company migration runs as a detached one-time worker so a dropped
command connection does not terminate it. The runner and its mandatory
foreign-key validation are unchanged. Recovery classification and resume receipts
are `.local/status-lookup-recovery-preflight.json` and
`.local/status-lookup-resume-20261017.json`.

A news-only public read exceeded the five-second deadline while the company
migration was consuming disk I/O. The company migration subsequently completed
in 1,202.113 seconds, with its prior schema and ledger preserved and its three
control-plane metadata counts unchanged (264,607 runs, 264,607 snapshots and
2,191 failures). The completed news migration was not rerun.

The first full public read after migration still exceeded its deadline. A
subsequent instrumented read identified the generic company outcome union as
remaining avoidable work. After limiting its two branches before the final
comparison, a full `data.get_dataset_status` 1.0.0 call returned all 79 records,
without truncation, in 0.864 seconds. Receipt:
`.local/status-lookup-acceptance.json`. These are observed timings on this host,
not a cold-cache or arbitrary-load guarantee.

### Inspector capture-attribution follow-up

Browser verification exposed a separate three-second, per-store deadline in
the Inspector's private capture-attribution reader. The public snapshot was
available, but historical shared-output lookups exhausted the company budget
and left company retention rows unknown. The plan scanned output links and
loaded full run records repeatedly. A timestamp-bounded query experiment did
not satisfy the budget and was discarded; `inspector_retention.py` remains
unchanged.

Registry 2.100.0 adds only `company:0021_status_run_lookup` and index
`ingestion_runs_status_capture(run_id, status, snapshot_id, dataset_id)`.
Both run joins in the unchanged attribution query use this covering index,
avoiding full run-table loads. The original successful-run, primary-snapshot,
explicit-output-link, timestamp and binary-ID conditions remain intact.
The exact predecessor is registry 2.99.0 and both tool catalogs are unchanged.
Candidate SHA-256:
`5b4148e5e0d7cb4d8d913c9fdff3cec64cf2649b7d8793ad9cc7b3f4eae573b3`.

The focused checks now include four original lookup/outcome regressions, three
new covering-index migration/replay/attribution/generator checks, the same thirty public
reader/route/page checks, and eight existing Inspector retention checks. All
45 checks passed on isolated fixtures. The new attribution fixture includes
1,000 direct and 1,000 shared captures and the reader's actual deadline path.
The latest generator candidate is byte-identical and leaves both catalogs
unchanged. Independent review passed for the exact index and activation scope.
Registry 2.100.0 was published through the generator and the Inspector pin was
updated before launching the detached company-only worker. Ten focused
post-publication fixture/route checks passed.

The first post-publication generator check exposed lexical version thresholds
that treated `2.100.0` as older than historical tool profiles. An exact mapping
of the new index-only profile to the already-reviewed `2.99.0` catalog profile
corrects those thresholds without changing any previous profile comparison or
the actual registry identity, hash, migration list or publication target.
Published bytes were not overwritten with the stale candidate. The new
generator regression and `--check` now pass with both catalogs unchanged;
independent review classifies this as an exact-profile compatibility change.
Company 0021 completed at `2026-10-17T05:06:48.631271+00:00`; its prior schema
and ledger remained intact and its metadata counts were unchanged. Receipt:
`.local/status-run-lookup-activation.json`.

### Fresh-read follow-up

The first instrumented public read after 0021 still took 21.423 seconds and
exceeded its deadline. The market capture join consumed 11.409 seconds and the
Alpaca news outcome lookup 6.696 seconds. The private reader also exhausted the
company budget while scanning shared-output membership. Earlier warm timings
therefore did not establish final acceptance.

The public capture reader now first selects the newest raw timestamp/binary-ID
snapshot, checks its linked run, and uses it if that run succeeded. Otherwise
it falls back to the original eligibility query. This preserves selection and
malformed-metadata handling while avoiding joins against older run histories.
A subsequent full public read took 2.985 seconds; this is an observed repeat
timing, not final fresh-read acceptance.

The private reader applies the same successful-newest-candidate proof to its
direct branch, binding that branch to the winning snapshot primary key before
comparing it with shared captures. A missing or non-successful candidate keeps
the full original branch. This addresses the separately observed market
capture-detail timeout without another store migration.

The prepared registry 2.101.0 adds only company 0022
`ingestion_run_outputs_dataset(dataset_id, run_id)` and news 0012
`current_multi_source_outcomes_status(attempt_id, recorded_at DESC, outcome_id DESC)`.
The news query materializes a narrow winning ID before fetching its full record.
It uses the covering index explicitly when present because SQLite otherwise
prefers its non-covering unique attempt index; older stores retain the original
query. Outcome kinds, raw timestamp ordering and binary-ID ties are unchanged.

The private shared-capture query excludes run anchors absent from the capture
ledger before loading full snapshots. This is a necessary consequence of its
existing `snapshot.dataset_id=run.dataset_id` condition. An immutable metadata
inventory found 87,808 analyst runs anchored to observations but snapshots only
under the evidence dataset, making those repeated row loads avoidable.

Registry 2.101.0 projects exactly to 2.100.0; both catalogs remain unchanged.
The exact catalog-profile alias also includes 2.101.0. Candidate SHA-256:
`0a4ec40cda7366970b9c5a7e9daada3f7701e863cffe18deeb24377d59834e62`.
The three lookup modules passed 12 checks, and the adjacent public/news/private
reader modules passed 28 checks, including original-query oracle parity for
the anchor filter. A six-check recheck passed after final test edits. The
stable baseline then passed six independently run checks in 13.778 seconds;
source review found no remaining publication or bounded-activation blocker.
All fixture databases were temporary and no provider or scheduler was accessed.

Registry 2.101.0 was published through the generator and the Inspector pin was
updated. A detached one-time worker applies only the two remaining migrations,
classifies existing completed ledgers before acting, and preserves prior
schema/ledger evidence. Its receipt and log are
`.local/status-evidence-lookup-activation.json` and
`.local/status-evidence-lookup-activation.log`. Activation completion and first
post-activation public/private/browser measurements remain pending.

After publication, all 12 selected checks passed in 22.182 seconds: current
generator bytes, four Inspector data-status routes and the unified status-page
module. This confirms the published 2.101 registry and unchanged catalog bytes;
it does not substitute for post-activation store timings.

The private direct-candidate follow-up passed all 12 Inspector retention tests,
three run-index checks and five current evidence-index checks. The added cases
compare against the original query for raw-candidate success, running/orphan
fallback, binary ties, a newer shared winner and malformed winning metadata;
the plan assertion confirms direct primary-key lookup. The focused set totals
54 unique checks across the lookup, reader, route and page modules.
Independent source/offline-evidence review also found no blocker in that final
private-reader correction. The reviewer independently ran the earlier six
checks. WSL refused its later new connections, so the integration owner ran
the selected 12-test Inspector module through the surviving native session
(all passed in 4.937 seconds); the reviewer inspected that output and final
oracle-test diff without claiming independent execution of those 12 checks.

### Market time index and shared-anchor seek

The two 2.101 migrations completed at `2026-10-17T05:51:17.414431+00:00`.
News 0012 took 266.455 seconds and company 0022 took 1,118.136 seconds;
prior schemas and ledgers were preserved and metadata counts were unchanged.
The first subsequent public read still exceeded its deadline in 20.824 seconds:
the market raw-capture selection consumed 17.155 seconds. The repaired news
outcome lookup took 0.242 seconds. Private capture attribution took 7.307 seconds
with timeout-related unknowns. These failed acceptance receipts are preserved
in `.local/status-evidence-public-acceptance.json` and
`.local/status-evidence-retention-acceptance.json`.

Registry 2.102.0 adds only `market:0014_status_capture_lookup`: the same
snapshot dataset/time, covering run metadata and output dataset indexes already
used by the company store. Its exact predecessor is 2.101.0 and both tool
catalogs remain unchanged. Candidate SHA-256:
`27b2511d9b9bb7ecb68c5236ffa8175057533ff9d152c01bf1cff5326603c979`.

The earlier private query's distinct-snapshot anchor filter is replaced by an
output-first map of possible shared anchors, computed once for the store's
fixed Inspector targets inside the existing three-second deadline. The map is
only a superset: each anchor is searched in capture-time order with every
original successful-run, primary-snapshot, same-run, same-dataset and explicit
output condition before its limit. The direct winner and per-anchor shared
winners retain the original raw-time and binary-ID final ordering in SQLite.
Historical anchor IDs need not belong to the current registry. Invalid winning
metadata still yields unknown rather than skipping to older data.

A company-only diagnostic of this new private query completed in 0.235 seconds
with no timeout-related unknowns. This is a repeat diagnostic, not final
fresh-read acceptance.

The three new market checks passed in 12.886 seconds, and the four adjacent
lookup/Inspector modules passed 25 checks in 23.483 seconds. Fixtures cover
exact predecessor projection, populated migration and replay, unchanged facts,
triggers and ledgers, query plans, multiple historical anchors, newer ineligible
captures, malformed winning metadata and binary ties. The independent reviewer
found no publication or market-only activation blocker after inspecting the
native source and test evidence. Its own WSL connection failed; the integration
owner executed its selected 16 checks on the stopped baseline, all passing in
7.802 seconds. That is independently reviewed evidence, not reviewer execution.

Registry 2.102.0 was published through the existing generator, the Inspector
pin was updated, and generated artifacts checked byte-identical. The prepared
one-time activation script is `.local/apply-status-market-lookups.py`, with
receipt `.local/status-market-lookup-activation.json`. Only market 0014 remains
to be applied; mandatory locking and foreign-key checks remain enabled.
Completed activation and final public/private/browser evidence are pending.

Post-publication checks passed: 31 tests in 16.152 seconds covering the current
generator bytes, public retained-status reader, news reader, four Inspector
routes and unified status page. The focused set totals 58 unique checks; the
16 reviewer-selected checks are a subset. The market-only worker started at
`2026-10-17T06:09:06.667756+00:00`; its receipt records the exact pre-migration
ledger and schema. Final activation acceptance remains pending.

### Completed market activation and sparse historical links

Market 0014 completed at `2026-10-17T06:20:55.709408+00:00` in 707.320 seconds.
Its prior ledger and schema were preserved; metadata counts remained 73,640
runs, 73,640 snapshots, zero failures and 151,480 output links. The first public
read returned all 79 records without truncation in 1.427 seconds. Receipt:
`.local/status-market-public-acceptance.json`, with the explicit 1.0.0 contract
and 2.102 manifest saved beside it.

The private read took 4.809 seconds and still left `market.stage10.universes`
unknown. The preceding instrument lookup walked 71,524 daily-price snapshots
to find a rare historical output link, taking 2.731 seconds. An immutable
metadata check found only eight total instrument output links and one universe
output link. This failed private receipt remains in
`.local/status-market-retention-acceptance.json`.

The final query-only correction probes at most 65 target output links when the
dataset output index exists. For at most 64 links it starts each shared branch
from those links; otherwise it retains the capture-time seek. Both branches
apply every original eligibility condition before raw-time/binary-ID ordering
and LIMIT. The probe selects a plan; it never caps returned capture history.
Older stores retain the original path and the three-second budget is unchanged.
No further migration or registry change is needed.

The new regression places one eligible old link behind 20,000 newer same-anchor
captures and checks original-query parity within a bounded SQLite work budget.
The dense 65-link fallback and indexed malformed/eligibility oracle cases also
pass. All 15 final Inspector checks passed in 1.915 seconds; the three market
and eight adjacent run/evidence checks passed on the same product source.
The focused set now totals 60 unique passing checks. Final private performance
and browser acceptance remain pending.

### Final acceptance — October 17, 06:35 UTC

The final private capture-detail read completed in 0.411563 seconds with no
unknown results. Its receipt and log are
`.local/status-sparse-retention-acceptance.json` and the adjacent `.log`.
Together with the 1.426830-second public read of all 79 records, these satisfy
the bounded acceptance checks. These are observed timings; prior diagnostics
had warmed operating-system caches, so they are not a cold-cache guarantee.
The existing time budgets and missingness rules remain unchanged.

The nonauthor reviewer found no blocker in the final query-only correction,
including sparse and dense plan selection, original eligibility and ordering,
and malformed-winner behavior. Its own new WSL connections failed; it reviewed
the final native source and integration-owner test output, including the 15
passing final private-reader checks. This is independently reviewed evidence,
not independent execution of that final test run.

The exact prior manual Inspector process was stopped and restarted at
`2026-10-17T06:30:36.121076+00:00` with the existing project root and loopback
port 8766; the replacement PID was 3118960. No recurring service or timer was
changed. The refreshed browser displayed registry 2.102.0, the stored-data
table with 57 active datasets out of all 79, and zero unknown datasets. A second
reload completed with the normal styled page and a snapshot timestamp of
`2026-10-17 02:34:40.945468 EDT`. The screenshot was saved as
`status-snapshot-repaired.png` in this task's visualization directory.
Genuine missing or old captures remain visible rather than being hidden.

The repair is live locally. No provider collection, provider retry, population
repeat, recurring-unit change, external deployment, commit or push occurred.
The earlier unrelated calendar/focus equal-height CSS change remains preserved.
