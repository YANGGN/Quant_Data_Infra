# Cross-asset ETF options expansion — October 13, 2026

## October 14 night: reference repair and dashboard completion

The user's subsequent instruction explicitly requests fixing the remaining
reference-validation gaps and finishing dashboard synchronization. The scope is
the 1,096 reference gaps in the completed retry status, all in 2016: EFA 3,
HYG 1, IEF 205, LQD 199, SLV 10, UNG 252, USO 252 and UUP 174. The separate
157 `eod_no_data` gaps and all already-published sessions are excluded.

The first of an at-most-eight Theta stock-reference request scope returned
`PERMISSION_DENIED` (stock history requires a different subscription); that
path stopped after one request and one authentication, with zero response
bytes. No upgrade, repeated denied request or alternate Theta access was used.
The aggregate Theta count is now 66,649 requests and seven authentications;
the prior byte totals and original deadline remain unchanged.

Eight bounded requests to the existing FMP provider's
`/stable/historical-price-eod/non-split-adjusted` endpoint succeeded, returning
216,663 bytes. The scope was one exact 2016 range for each affected ETF, at most
eight requests, 16 MiB, 600 seconds and zero retries, through the existing
credential resolver and shared account allowance at backfill priority. Full
bodies and receipts are retained in
`.local/cross-asset-options-20261012/reference-repair-20261015/`. No market-store
write or recurring-unit change is part of this operation.

The additive `fmp_non_split_adjusted_close_v1` reference binding uses the
endpoint's `adjClose` field as documented by
[FMP](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-non-split-adjusted).
This endpoint-specific field is an unadjusted historical close; it is not the
dividend-adjusted `adjClose` from a different endpoint. Original response hash,
row, date, symbol, price basis and capture receipt are preserved. Complete
body hashes, date bounds, unique dates and positive consistent OHLC are checked
before extraction; the capture builder checks the retained binding again.
No split factor is applied to retained full-EOD closes.

Direct unadjusted price evidence does not require the adjusted-price fallback's
put/call parity compatibility inference. The old fallback retains its exact
checks. New references remain date-only, locally captured current history,
with unknown historical release time and unverified option deliverables.
Greek reference timestamps, IV quality, option dates and OI cutoffs retain
their existing checks. All 1,096 candidates are price-only; missing IV is not
calculated or filled. Only missing option captures may be published, through
the existing physical lock and replay-safe publisher. Original source journals
and frozen reference files remain untouched.

Dashboard completion uses the existing private Site, verified TLS, saved-data
projection and acknowledged cursor, at most 80 batches of 500 points within
1,200 seconds, two state reads and zero automatic retries. It makes no provider
request or canonical-store write. Actual preparation, publication and remote
read-back outcomes are recorded below when obtained.

### Verified completion — October 15, 03:03 UTC

All 93 focused tests passed locally (20.069 seconds) and independently under
network/credential/store-isolation guards (31.108 seconds), with no skips.
The reviewer verified all 3,308 pinned input hashes, 1,096 candidate hashes and
reference bindings. A dashboard deadline-admission defect was corrected before
execution; independent slow-read, journal-delay, late-admission, final-read and
successful-path checks passed. No other blocker remained.

Missing-only publication completed at `2026-10-15T03:01:24.872883+00:00`.
All 1,096 reference gaps were recovered as captures 67,106–68,201. Locked
immutable read-back verified preservation of all 67,105 previous current
captures, the exact new semantic hashes, 132,284 new selected contract rows,
and zero non-null IV values among those rows. The original 30,273-session
population now has **30,116 saved ETF-days and exactly 157 `eod_no_data` gaps**.
The remaining keys exactly match the previous no-data set. Publication made
zero provider requests and did not change market prices or recurring units.

Dashboard synchronization completed at `2026-10-15T03:02:34.189879+00:00`
(October 14, 23:02 Eastern). It sent 15,123 outstanding points in 31 acknowledged
batches, advancing the remote cursor from 53,078 to 68,201. Final remote state
confirmed cursor 68,201, derivation `options_daily_chart_v1`, source cutoff
October 14, and `last_synced_at` `2026-10-15T03:02:33.796Z`. The operation
returned `up_to_date`; verified TLS succeeded, with no automatic retry.

The consolidated private receipt is `reference-repair-20261015/completion.json`
under the original work directory. Detailed publication and dashboard receipts
are `repair-completion.json` and `dashboard-completion.json`. All original
completed-run status files are historical evidence and remain unchanged;
this completion supersedes their gap/sync status. No authorized repair or sync
work remains running. The 157 no-data gaps are not silently treated as filled.

## Decision and scope

The user explicitly requested checking options coverage, backfilling the longest
available history, adding daily collection, and adding the intraday dashboard
for GLD, SLV, TLT, IEF, HYG, LQD, USO, UNG, EFA, EEM, UUP, and IBIT.
This scoped decision extends the former 15-ETF roster; it does not authorize
repeating the earlier populations, purchasing provider access, or changing
unrelated recurring units. The original collector policies remain frozen.

Migration `options/0003_cross_asset_etf_roots.sql` adds precisely these roots to
the optional options store. Earlier migration bytes/checksums are unchanged.
The companion registry becomes 1.2.0. Existing facts, identifiers, raw-versus-
adjusted price semantics, missingness, and replay rules are preserved.

## Coverage evidence

Private durable evidence is under `.local/cross-asset-options-20261012/`.
Discovery completed with 59 data requests and 32,527,833 response bytes within
a 64-request, 256-MiB, 600-second, one-login, zero-retry bound. Every root returned
combined EOD Greeks and open interest for October 9, 2026.

A distinct floor check completed with 36 requests and 4,280,701 response bytes
within a 40-request, 512-MiB, 900-second, one-login, zero-retry bound. GLD EOD for
June 1, 2012 returned `PERMISSION_DENIED`. All 11 older roots returned price-only
EOD and open interest for January 4, 2016; combined Greeks returned no data.
IBIT returned combined Greeks for November 19, 2024 and no open interest for
that first session. Catalog visibility is not entitlement or IV availability.

## Finite historical work

The bounded history job covers only the 12 additions through October 12, 2026,
starting January 4, 2016 except IBIT starting November 19, 2024. The maximum is
100,000 data requests, 128 GiB of provider response bytes, 72 hours elapsed from
first admission, and 256 authentication attempts. It uses zero retries, a
25-GiB options-store ceiling and 20-GiB disk reserve. Discovery/floor responses
are reused by exact method/parameters and retained evidence hashes.

Provider admission yields to normal live and scheduled collection. Requests
precede physical store locks; publication is missing-only and replay-safe.
Existing local price references may support the established price-only
fallback. Missing or incompatible references remain explicit gaps; IV is
never fabricated. A separate dashboard baseline bootstrap is limited to these
12 roots and 20 preceding completed sessions, at most 12,000 requests, 32 GiB,
24 hours and zero retries. These two provider jobs run sequentially.

## Recurring collection and dashboard

The additive daily policy `theta_daily_and_weekly_v3` contains the original 15
plus these 12 roots. Daily remains Tuesday–Saturday 07:15 America/New_York,
collecting the prior session, capped at 56 requests/2 GiB/1,200 seconds.
Weekly missing-only repair remains Sunday 03:00 America/New_York, capped at
272 requests/8 GiB/3,600 seconds. Both use one worker and zero retries. A new
`activation-cross-asset.json` pins future first slots and the exact policy hash;
historical v2 activation and period receipts are preserved.

The monitor roster adds the 12 as market ETFs at a 30-minute base cadence,
within its existing cycle/session limits and priority policy. Existing 2,266
members and their provenance are preserved. The dashboard expands to 27 ETFs
in six asset groups, retaining missingness and freshness labels. It is built
from an isolated checkout of the deployed Site; unrelated local Site edits
are not included. Existing Site audience remains unchanged.

The six public daily Theta tools retain their original version-1, 15-root
contracts. Expanded collection and dashboard history use the 27-root internal
readers. Saved intraday public tools already use the configured monitor roster.

## Activation evidence

Independent verification, store backup/migration, activation slots, finite-job
outcomes and Site deployment identifiers are recorded below when completed.
The design and test artifacts above alone do not assert live activation.

An independent reviewer reproduced 89 scoped offline cases: 88 passed and one
SDK-dependent protobuf check was skipped in system Python. The test harness
blocked network/credentials and rejected database paths outside explicit
temporary fixtures. Migration 0003 SHA-256 was verified as
`c82c56f985fb136a22f48c2a1b0701292af18c637405a8b17db561d3ef604776`.
Migration, daily-v3 and daily-projection changes had no actionable blocker.
The reviewer did not verify live activation, the new history runner, or the
staged monitor roster. Site source advanced concurrently from version 17 to
18; the ETF UI must be reconciled with version 18 before publication.

### Pending host recovery

WSL repeatedly failed command startup with `Wsl/Service/0x8007274c`.
A host check reported 568,352 KiB of free physical memory out of 16,364,512 KiB.
The backup copy exists at `options-before-migration.sqlite` (8,717,213,696
bytes), but both validation processes exited without successful receipts;
it is **not a verified backup**. No options migration, v3 activation, roster
activation, baseline run, history run, or ETF Site deployment has occurred.

To preserve normal v2 collection while blocked, the live companion registry
was restored to its exact clean-at-entry 1.1.0/two-migration bytes. The reviewed
1.2.0 proposal is retained at
`.local/cross-asset-options-20261012/options_registry.activation.json`, SHA-256
`0e39da54dcab1868b89c11a9ffd1a2ed52924445902acc771e7104b7efc3b713`.
Restore that proposal only as part of the verified migration/activation flow.
Offline tests of the expanded store require the proposed registry in isolated
fixtures; the live runtime deliberately remains on the original registry.

The staged monitor roster independently preserves all 2,266 original entries
and adds exactly the requested 12 at market/30. Its SHA-256 is
`875140c5501394dddccee844dfed2de2f609005d97ee7b1372ba2fdc5e5478b5`.
The finite history plan contains 30,273 symbol/session pairs. Eleven symbols
have complete saved-price reference artifacts; UNG's public saved-price read
returned an error and needs diagnosis, without a new provider fetch.

Independent runner review found two blockers, both now corrected in source
but awaiting fresh tests and re-review: failed/unknown login outcomes must
block restart authentication, and valid Greeks must survive missing OI.
The earlier nine passing author tests precede these corrections. Do not treat
them as verification of the final runner. Original v17 UI tests passed, but
the port into `.local/cross-asset-site-v18` is unconfirmed and must be reconciled
before tests/build/publication. Its opened base commit is
`61465b82a62790f5e533275c2c305e0e7f87539d`.

### Recovery and activation — October 13, 14:13 UTC

This section supersedes the pending state above. WSL recovered without a host
restart or changes to unrelated recurring units. The original stale backup
remains historical evidence. A fresh backup at
`options-before-migration-current.sqlite` passed integrity and foreign-key
checks at 14:08:34 UTC, with 8,720,990,208 bytes and capture high-water 38,058.
The validation ran in a finite scope with bounded memory. Its complete receipt
is `backup-current-receipt.json` under the private work directory.

The reviewed 1.2.0 registry was installed and migration 0003 committed. Before
and after digests match for all 38,058 captures, 1,030,518 option contracts and
38,058 current pointers. Earlier ledger entries are identical. Migration
validation checked foreign keys before commit. `migration-activation.json`
records the exact evidence; no provider request was made by this operation.

Daily v3 activation was written at `2026-10-13T14:13:03.147338+00:00` with policy
SHA-256 `84998d8de240f9e0a2c010df4b67b6a9042ad866541299d1983e776574a0b781`.
The first expanded daily slot is October 14, 07:15 Eastern; the first expanded
weekly slot is October 18, 03:00 Eastern. A read-only systemd check confirmed
both timers enabled and active with these exact next slots. Existing timer
schedules are retained; no manual daily or weekly run was triggered.

The monitor roster was activated with the independently checked SHA-256 above.
The public `options.get_intraday_snapshot@2.0.0` read at 14:11:39 UTC returned
all 12 additions with October 13 observations captured at approximately
14:00:26–14:00:28 UTC, complete source coverage, freshness within the saved
30-minute cadence, and non-null 30-day IV proxies. Relative volume remains
`baseline_building`. The complete public envelope is retained as
`intraday-snapshot-second-check.json`; it made zero provider requests.

The finite 20-session baseline run `894e6db57ddf1defa3e1d7d5` was started with
the declared 12,000-request/32-GiB/24-hour caps and zero retries. It uses the
established one-request daytime admission and yields to live collection.
Deadline: `2026-10-14T13:47:19.353192+00:00`. At the 14:11 UTC check, nine of
240 ticker-days were saved and 231 remained pending; one request was in flight.
This is progress evidence, not a completion claim.

Independent review of the final history runner covered 13 unique offline cases.
Eight affected cases were rerun after the final protected-window correction;
all passed with no skips. Failed/unknown authentication and requests cannot be
silently retried; valid Greeks survive missing OI. Final runner SHA-256:
`1922800ee552feded8bd99868766d6d8ce8682797166e7393fe229d45af3cdd7`.
The staged roster review also confirmed exact preservation of the original
2,266 entries. UNG's saved-price response was diagnosed as an unavailable
market instrument, not a timeout. Its old price-only captures remain explicit
missing-reference gaps; no additional price-provider fetch was admitted.

The UI owner ported the eight ETF files onto deployed v18, preserving its
review queue, sector breadth and radar behavior. Validation passed 276
assertions, TypeScript and production build. Earlier desktop/mobile ETF visual
checks passed; a fresh v18 browser check was unavailable because the browser
inventory was empty. Source commit `18ca86c759da89a09a78c71bb49dea5758632067`
was packaged and deployed as Site version 19, deployment
`appgdep_6ace382085948191b47c70a0bb7ae282`, successful at 13:54:50 UTC.
The existing custom audience remained unchanged. Dashboard:
<https://quant-options-monitor.yanggainan.chatgpt.site>.

### Retained samples and finite history handoff — 14:17 UTC

The reviewed offline seeding script targeted 24 recent/floor symbol-days and
published 20 through the established missing-only publisher, with zero provider
requests or authentication attempts. All 12 October 9 captures and IBIT's first
November 19, 2024 capture were saved; the latter retains missing OI. Seven
January 4, 2016 price-only captures were also saved. SLV, USO and UUP floor
captures failed the existing reference cross-check; UNG lacked a saved spot
reference. The receipt reports `completed_with_gaps`, never 24 populated days.
Original discovery evidence was preserved. Receipt: `seed-retained-receipt.json`.

One bounded dashboard synchronization published all 20 new points and advanced
the acknowledged cursor from 38,058 to 38,078. The Site read-back confirmed
that cursor at 14:17:42 UTC. Receipt: `seed-dashboard-sync.json`.

The finite transient service `quant-data-cross-asset-history-20261013.service`
was launched after independent static review of its orchestration. It waits
for the existing baseline owner to release `baseline.lock`, no later than
October 14 at 13:52:19 UTC. It then rechecks the reviewed scope and runner hash,
invokes the finite history runner once with its original caps, and retains the
baseline owner lock to avoid concurrent background logins. It waits through
protected provider windows and performs no automatic retry. This is not a new
recurring unit. Its memory ceiling is 1 GiB, swap ceiling 256 MiB and outer
runtime ceiling 97 hours; the provider run itself remains capped at 72 hours.
Final Site sync is bounded to 80 batches/1,200 seconds.

At handoff the baseline remains in progress and long-history acquisition is
queued, not completed. The 30,273-session plan will preserve the 20 samples
already saved. Durable state is in `history-queue.json`, `status.json` once
admitted, `monitor-baseline-run.log`, `history-sequence.log` and
`handoff-status.json`. Neither queued state nor a running process proves that
all history was populated; final receipt and coverage/gap counts are required.

### October 13 evening: four-worker amendment

The user explicitly requested four parallel workers for the full historical
backfill after hours, preserving existing limits and avoiding duplicate
downloads or interference with live collection. This supersedes the original
one-worker setting only. The original authorization, manifest, date/symbol
scope, request/byte/authentication/store caps, zero retries and deadline remain
unchanged. The original 72-hour period began at
`2026-10-13T16:42:09.103902+00:00`; it ends October 16 at 12:42:09 Eastern.
An additive `parallel-amendment.json` binds four workers to the original
authorization hash and unchanged caps. It does not rewrite that evidence.

`quant_data/options/cross_asset_parallel.py` uses four bounded download slots
and one shared Theta login/channel, with thread-local transport receipts and
one global budget. The coordinator alone writes canonical captures and cleanup
records. At most four symbol-days retain buffers. Network work remains outside
database locks. Each request uses the existing background capacity/session
leases and live-intent checks; scheduled windows retain the three-minute RPC
headroom. A live generation change or scheduled pause drains the old session
before reauthentication. Failed/unknown requests or authentication never retry.

The default serial runner retains its behavior through a download-iterator
hook. Both paths recheck original elapsed time and storage limits before
publication, including cached-only responses. Graceful parallel shutdown stops
new admissions, drains in-flight requests to durable staged responses, and
leaves those responses available for missing-only resume.

The transition script uses the exclusive background-capacity lease to drain
the exact original worker before signalling it; live intent/session leases are
not seized. It then checks owner exit, journal identity/count parity, staged
or canonical coverage, and unchanged authorization/manifest bytes before
recording the amendment. The replacement is a finite, non-restarting service
holding the existing baseline/queue/run owner locks. Activation evidence follows
only after independent verification and the actual transition.

Validation scope is the history iterator, global accounting, staged resume,
authentication renewal and direct admission/transport consumers. The existing
admission test `test_daytime_clock_and_existing_job_guards` still asserts the
retired 19:15 daily guard and fails against the already-accepted 07:15 schedule;
it was not changed. New checks cover the current daily/weekly boundaries and
RPC headroom. Initial new-test failures were fixture corrections, not provider
executions. The pinned-SDK interleaved transport check passed after the system
Python run skipped it. No full-suite trigger applies to this domain-local hook.

Independent verification passed all 43 selected checks with the pinned SDK,
zero failures and zero skips; the unrelated stale daily-guard assertion above
was excluded. The reviewer checked the stable source hashes and both private
transition/launch scripts without provider or operational-store access.

The actual transition drained the original owner at 23:16:01 UTC with 3,787
completed ETF-days, 7,536 attempts and 7,536 matching receipts, no duplicate
units, one authentication and 8,302,143,644 received bytes. The cutover itself
made zero provider requests. Original authorization and manifest bytes remained
unchanged. The old process exited before the replacement started.

`quant-data-cross-asset-history-parallel-20261013.service` started at 23:16:17
UTC with PID 375932, four download workers, no restart policy and the existing
1 GiB memory / 256 MiB swap ceilings. The October 13 23:18:50 UTC activation
check observed an active service, a maximum of four simultaneous requests and
3,920 of 30,273 completed ETF-days. Across the appended journals, all 7,808
attempts and 7,807 received outcomes had unique request identities; every
received outcome was success, empty or no-data. Counts were sampled while the
job was active, so an in-flight request need not yet have a receipt. Two total
authentications had been consumed, and the original October 16 16:42:09 UTC
provider deadline remained unchanged. Full history was still running.

Durable operational evidence is in
`.local/cross-asset-options-20261012/parallel-cutover.json`,
`parallel-launch.json`, `parallel-activation.json`, `parallel-amendment.json`
and `parallel-source-hashes.json`; continuing progress is in `status.json` and
`history-parallel.log`. No recurring unit was changed by this amendment.

### October 14: one TLT retry and nonblocking unavailability

The user explicitly authorized one additional attempt of
`option_history_greeks_eod` for TLT on May 26, 2023, and resumption with four
workers. The user also directed that further provider `UNAVAILABLE` responses
must not stop the rest of this finite history population. This supersedes the
zero-retry rule for that exact request once, and the stop-on-unavailability
policy for this population only. All other provider errors, unknown attempts,
authentication failures, scope limits and operational protections remain gates.
The original authorization, manifest and parallel amendment remain immutable.

`quant_data/options/cross_asset_recovery.py` requires a separate
`unavailable-amendment.json`, bound to the original authorization, manifest,
unchanged caps, exact failed request identity and its retained receipt digest.
The one additional attempt is journaled before network admission and consumes
the existing cumulative request/byte/time budget. It grants no extra budget or
deadline extension. Restart reconciliation counts physical attempts, accepts
only this one duplicate identity with its original-receipt link, and rejects
missing receipts, other duplicates or a third attempt. Successful staged or
canonical data are reused.

A later `UNAVAILABLE`, including on the one approved retry, becomes a
`provider_unavailable` gap for that symbol-day; the remaining workers continue.
Such a gap is not published as an empty canonical capture or counted as saved
history. Its failure receipt and any successful partial response remain on
disk. After a crash before gap publication, the retained failure reconstructs
the gap without reissuing the request. There is no automatic retry of these
gaps. Normal shared-session, four-worker, live-priority, window/headroom,
authentication-cap and single-coordinator publication mechanisms are retained.
The default parallel runner retains its prior stop-on-failure policy.

Primary validation passed 36 focused recovery, parallel and original-history
tests in explicit temporary stores with network blocked. The 12 new cases
cover one retry, repeated unavailability, partial-response retention, crash
recovery, staged-success reuse, other-error and unknown-attempt stops, a third
attempt rejection, global accounting, authority binding, live priority and
the unchanged deadline. Independent review and actual activation are recorded
below only after completion. No shared store/schema or recurring unit changed.

Independent review initially found a shutdown race that could replace a causal
budget error with a sibling's stop sentinel. The parallel runner now records
the first causal error under its session guard before exposing shutdown, and
treats the inherited stop sentinel as a pause. The final primary run passed
37 focused tests; ten additional repetitions of the affected budget test also
passed. Independent recheck passed all 24 affected tests, retaining 18 earlier
unaffected passing checks: 42 distinct checks covered, zero remaining failures
or skips. Intermediate primary failures included corrected test placement and
one transient contention timeout in the existing fake transport's 50 ms store
lock check; the final complete selected run passed with that fixture unchanged.
No full-suite trigger applied. The retired daily-guard assertion remained
outside this gate.

The October 14 05:29:23 UTC operational preflight made zero provider requests
and reconciled 9,786 saved ETF-days, one existing quality gap, 19,537 requests,
18,255,736,021 response bytes and two authentications. Exactly 80,463 request
admissions remained within the original 100,000 cap, including the approved
retry. It used the existing `THETA_DATA_API` credential resolver and registered
`data/options.sqlite` publisher; no new credential or store binding was added.

The finite, non-restarting
`quant-data-cross-asset-history-resume-20261014.service` launched at 05:29:54
UTC. The approved TLT retry succeeded with 2,806 response rows; bounded canonical
read-back confirmed TLT May 26, 2023 was saved. At 05:30:59 UTC, PID 486623 was
active, four simultaneous requests had been observed, and 9,834 of 30,273
ETF-days were saved. There were exactly two attempts of the approved TLT
identity and no other duplicate identities. Original journal prefixes,
authorization and manifest bytes were unchanged. Three total authentications
had been consumed. The original deadline, 1 GiB memory / 256 MiB swap ceilings
and live protections were unchanged; full history remained running.

Evidence is in `.local/cross-asset-options-20261012/`:
`recovery-before.json`, `unavailable-amendment.json`, `recovery-source-hashes.json`,
`recovery-preflight.json`, `recovery-launch.json`, `recovery-activation.json`
and `history-recovery.log`. The prior failed service and its receipts remain
historical evidence; no recurring units were changed.

### October 14 morning: interruption accounting repair prepared

The user requested repair and resumption after the finite collector disappeared
around 02:07 Eastern. The last checkpoint reported 11,652 saved ETF-days.
Local logs show unclean system/user journal shutdown and a new user-service
manager at 02:08; they do not establish the underlying trigger. The transient
unit definition was absent when inspected. One later USO `UNAVAILABLE` had
correctly become a gap while the other workers continued.

Four attempts have no receipt or staged response: Greeks for EFA and EEM on
September 23, 2022, Greeks for GLD on September 22, 2022, and open interest for
UUP on September 23, 2022. There are 23,273 attempts and 23,269 receipts;
receipted response bytes total 21,402,974,666. Exact bytes consumed by the four
interrupted requests cannot be reconstructed from the retained evidence.

**Accounting decision pending:** the proposed repair charges 1 GiB for each
of those four requests against the existing 128 GiB cap, records their
symbol-days as `interrupted_no_receipt` gaps, and never reissues them. This
4 GiB charge is an explicitly proposed conservative allowance, not measured
bytes or a proven historical upper bound. The request to fix/resume does not
by itself settle this accounting decision. The user was asked to approve it;
no approval record, live amendment or launch is created before that answer.

The prepared `cross_asset_durable.py` keeps measured bytes distinct from the
allowance and active reservations. Before an RPC it durably reserves 128 MiB
inside the original aggregate cap. As messages arrive it renews the reservation
before less than the existing 64 MiB maximum message size remains available
for the next message. Only a durably appended provider receipt releases unused
reservation. A future interrupted request retains its last reservation debit;
a version-marked attempt with no reservation proves no RPC was started and
receives zero response-byte charge. Unmarked unknown attempts remain blocked.
No unknown attempt is downloaded again. The four legacy exceptions are bound
to exact attempt hashes and preserved journal prefixes; other retry rules,
the original deadline, scopes and live guards remain unchanged.

The prepared finite service has no timer, install target or automatic restart.
Its definition will be linked persistently so a service-manager restart does
not erase the definition; that does not cause automatic collection on boot.
Runtime is bounded by the remaining original deadline plus the existing
1,200-second final sync allowance. Existing 1 GiB memory / 256 MiB swap limits
are retained. Private preparation/launch files are `prepare_durable.py`,
`run_durable.py` and `durable-service.template` under the existing work root.

Primary validation passed 49 focused tests spanning durable reservations,
interruption handling, prior recovery and parallel/serial history consumers.
The fake transport's physical-store lock probe now allows one second for the
separate coordinator's publication to drain; its prior 50 ms timeout repeatedly
failed under valid concurrent publication. A lock held by the requesting
worker still times out. This is a fixture change only. Independent verification
and any authorized activation are recorded separately after completion.

Independent review found one durability blocker: fsync of a newly created
reservation journal alone does not persist its directory entry. The corrected
runner creates and fsyncs that journal, then syncs its parent directories before
constructing the budget or authenticating, on every startup. A focused failure
test proves a directory-sync error admits zero authentications and requests,
and a subsequent startup syncs successfully before admission. The corrected
primary gate passed 50 tests in 23.032 seconds; `git diff --check` passed.
The reviewer's initial 50-test gate also covered the parallel transport.
Independent recheck passed all 13 affected durable tests in 2.828 seconds,
retaining 38 unaffected earlier passes: 51 distinct checks covered, with no
remaining findings. The corrected source SHA-256 is
`b5e1232f740bda652db54f8b7dabf0191d0a77c3adbb016c0baf726ee02e860c`;
the durable-test SHA-256 is
`a5cce8ab3230c38a58acf53b548bcbfd9feb6db9279ace375245d70eaccccaff`.
Operational scripts received static review only. At 12:21:01 UTC (08:21
Eastern), both the prior resume unit and proposed durable unit were inactive,
not found, with MainPID zero. No provider work or service launch occurred for
this repair while the accounting decision remains pending.

### October 14 afternoon: approved durable continuation activated

The user answered "Yeah go" to the explicit accounting/resumption proposal.
The approval record at 18:53:58 UTC authorizes the estimated 4 GiB charge inside
the original byte cap, the four interruption gaps, and the same finite
four-worker continuation. This resolves the morning's pending decision without
granting additional retries, requests, dates or duration.

The 18:54:17 UTC provider-free preflight reconciled 11,652 saved ETF-days,
23,273 attempts, 23,269 receipts and three authentications. Measured receipted
bytes remain 21,402,974,666; accounted bytes are 25,697,941,962 after the
4,294,967,296-byte allowance. Exactly 76,727 request admissions remain inside
the original 100,000 cap. The consumed TLT exception remained the only duplicate
request identity. Original authorization/manifest bytes, journal prefixes and
the independently reviewed source hashes matched.

The finite `quant-data-cross-asset-history-durable-20261014.service` was linked
and started once at 18:55:05 UTC. At 18:56:03 UTC it was active/running with
PID 234980, four configured workers, zero in-flight requests, `Restart=no`,
1 GiB memory and 256 MiB swap ceilings. Its persistent definition is linked,
not enabled for boot. RuntimeMaxSec is 166,024 seconds from launch, preserving
the original October 16 16:42:09 UTC provider deadline plus the already approved
1,200-second final sync allowance.

All four interruption gaps were recorded, bringing the retained gap count to
six. Saved coverage remained 11,652 of 30,273 ETF-days. It was within the
protected market-hours window: no new login or data request occurred, and the
durable reservation journal remained empty. The active workers wait until the
next eligible window, October 14 at 17:00 Eastern; actual post-window downloads
were not observed during this activation check. Existing admission/priority
locks, daily/weekly guards, provider-unavailable continuation and all remaining
caps are unchanged. No recurring units or Site deployment were changed.

Evidence under `.local/cross-asset-options-20261012/`:
`interruption-accounting-approval.json`, `interruption-amendment.json`,
`durable-before.json`, `durable-source-hashes.json`, `durable-preflight.json`,
`durable-launch.json`, `durable-activation.json`, the linked service definition
and `history-durable.log`. The previous 51-check independent gate remains valid
because the reviewed implementation and operational scripts were unchanged.

### October 14 evening: user-requested WSL restart and continuation

The user explicitly requested a WSL restart after status commands timed out.
`wsl.exe --shutdown` completed after initially remaining pending; a new VM
process appeared. At October 15 01:04:36 UTC (October 14 21:04 Eastern), Ubuntu
and its user systemd manager both reported `running`, with boot ID
`6394adf2-4c45-4382-9726-72c130f760ef`. The finite collector definition survived
as intended and was inactive after boot; it did not auto-start.

A provider-free preflight reconciled 28,887 saved ETF-days, 1,160 gaps and 226
remaining sessions. Three additional marked requests had been interrupted:
SLV EOD February 2, 2016, EEM OI February 3, 2016, and GLD OI February 2, 2016.
Their durable 128 MiB reservations each remained charged under the approved
policy, for 384 MiB total; no new estimated allowance or retry was introduced.
Total interruption accounting is now 4,697,620,480 bytes, consisting of the
earlier approved 4 GiB estimate and these three retained reservations. There
were 62,365 attempts, four authentications and 37,635 admissions remaining
inside the original request cap. Preflight made zero provider requests.

The existing finite service was manually resumed once at 01:06:04 UTC under
the already approved workload. RuntimeMaxSec was reduced to 143,765 seconds
to retain the original provider deadline plus the existing final sync allowance;
the reviewed runner, source hashes, symbols, dates and all other limits remain
unchanged. At 01:07:03 UTC, PID 2051 was active, four simultaneous requests had
been observed, and saved coverage had advanced to 28,938 ETF-days with 1,205
gaps and 130 sessions remaining. Five authentications had been consumed.
The original authorization and manifest bytes were preserved, and the approved
TLT identity remained the only duplicate request. Full history was still
running at this read-back; no completion or dashboard-sync claim is made here.

Evidence: `.local/cross-asset-options-20261012/`
`wsl-restart-preflight-20261015.json`, `wsl-restart-launch-20261015.json` and
`wsl-restart-activation-20261015.json`. No implementation changes were needed;
the existing independent verification remains applicable.

### October 14 night: full pass completed; one gap retry requested

The original history pass completed at October 15 01:08:14 UTC (October 14
21:08 Eastern): 29,008 ETF-days saved and 1,265 gaps, accounting for all 30,273
planned sessions. It consumed 63,018 data requests, 42,122,598,844 measured
response bytes and five authentications. Total accounted bytes were
46,820,219,324 including retained interruption charges. Its subsequent dashboard
sync failed at 01:08:48 UTC with `SSLV3_ALERT_BAD_RECORD_MAC`; this did not
undo the completed local history. The original pass must not be repeated.

The user then explicitly requested "can you retry the gaps first?", referring
to those 1,265 recorded gaps. This permits one new pass over that exact
symbol/date population, up to one additional attempt per method/parameters,
with a ceiling of 3,795 data requests (three possible methods per gap). The
new allowance is inside the original remaining 100,000-request, 128-GiB,
256-authentication and October 16 16:42:09 UTC limits; it does not reset or
extend them. The 29,008 saved sessions are excluded from fresh downloads and
publication remains missing-only. Dashboard synchronization is deferred until
after the gap pass.

Initial gaps: 157 `eod_no_data`, six `provider_unavailable`, seven
`interrupted_no_receipt`, 840 `fallback_reference_not_crosschecked`, 252
`missing_contemporaneous_spot`, and three `fallback_reference_basis_mismatch`.
Thus 1,095 gaps are validation/reference failures; this pass does not weaken
those checks or fabricate IV and may retain many gaps. It introduces no new
price provider or reference semantics.

`cross_asset_gap_retry.py` isolates new attempts, receipts, reservations and
gap results under `.local/cross-asset-options-20261012/gap-retry-20261014/`.
Its authorization is bound to the completed parent journals/status/manifest
and exact gap units. Original evidence is preserved. Known bytes, retained
reservations and authentication counts carry into the shared aggregate caps.
The same four-worker transport, after-hours windows, priority admission,
physical-store publisher and durable reservations are reused. A crash cannot
admit another pass over an attempted request; a receipted response lost before
staging becomes an explicit `response_not_staged` gap. `UNAVAILABLE` remains
nonblocking for the other units. The finite service has no timer, boot enablement
or automatic restart, with the existing memory/swap bounds and original deadline.

Primary validation passed 66 focused tests in 33.498 seconds, including 15 new
gap-scope/replay/crash/accounting/validation/admission checks and all directly
affected durable, recovery, parallel, serial and transport regressions. Source
and private preparation/runner/template files are frozen for independent review.
No gap-retry provider request or activation has occurred at this preparation
checkpoint; subsequent evidence is recorded below after the gate passes.

Independent verification passed the same 66 distinct checks in 35.394 seconds,
with zero failures/skips and no findings. All 15 pinned source/private-script
hashes matched and Python sources parsed. The reviewer used an explicit
temporary root and audit guards against network, subprocess, credential,
project-write and non-fixture SQLite access. Private scripts received static
review only; operational evidence follows separately.

The October 15 02:23:05 UTC preflight confirmed the exact 1,265 gaps, original
63,018 requests, 46,820,219,324 accounted bytes and five authentications,
with zero provider requests. It uses the existing `THETA_DATA_API` credential
resolver and registered `data/options.sqlite` publisher. The finite
`quant-data-cross-asset-gap-retry-20261014.service` launched once at 02:23:27
UTC. RuntimeMaxSec was 137,922 seconds, retaining the original deadline.
No dashboard request is made by this wrapper.

At 02:24:50 UTC (October 14 22:24 Eastern), active PID 37515 had observed four
simultaneous requests and canonical read-back confirmed eight recovered gap
sessions. Total saved coverage was 29,016; 284 retried sessions still had gaps
and 973 remained to check. There were 720 unique new request identities, all
inside the authorized gap scope, with one additional shared authentication
(six aggregate). The completed parent files and pinned source hashes were
unchanged. All original limits, quality checks and live protections were
retained. This is an activation check, not a completed-retry claim.

Evidence under the separate `gap-retry-20261014/` directory includes
`user-decision.json`, `retry-authorization.json`, `source-hashes.json`,
`preflight.json`, `launch.json`, `activation.json`, the service definition,
`status.json`, `run-state.json` and `run.log`. Runtime results remain distinct
from the completed original pass.

The single gap retry completed at October 15 02:31:06 UTC (October 14 22:31
Eastern) and the service exited successfully with status zero. It made 3,630
new requests and received 243,841,213 response bytes. Exactly 12 gap sessions
were recovered and 1,253 remained: 157 `eod_no_data`, 841
`fallback_reference_not_crosschecked`, 252 `missing_contemporaneous_spot`, and
three `fallback_reference_basis_mismatch`. No provider-unavailable or interrupted
outcome remains in the retry pass. LQD September 30, 2016 changed from
`provider_unavailable` to `fallback_reference_not_crosschecked` after the retry
returned data. No validation requirement was weakened.

At 02:32:22 UTC, provider-free locked immutable read-back confirmed 29,020
saved ETF-days and the exact 12 recovered keys. Recovered keys plus the final
gap set partition all 1,265 original gaps without overlap. All 3,630 new
attempts had one matching receipt, with no duplicate retry identities.
Reconstructed aggregate totals match final status: 66,648 requests,
42,366,440,057 measured bytes, 47,064,060,537 accounted bytes and six
authentications; no in-flight reservations remain. Parent files and all 15
reviewed source/private-script hashes are unchanged. Final evidence is
`gap-retry-20261014/completion.json`; no provider request was made by validation.

This authorized retry pass is exhausted and must not repeat automatically.
The remaining 1,096 reference-validation failures and 157 no-data gaps retain
their evidence. Dashboard synchronization remains deferred; this pass made
no dashboard request and does not resolve the earlier SSL failure.
