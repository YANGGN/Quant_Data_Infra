# Weekly historical-price repair

Decision: user-approved September 15, 2026. Implementation and activation evidence
are recorded below when verified.

## Authorized routine

A separate per-user `quant-data-weekly-price-repair.timer` runs Saturdays at
02:00 America/Toronto. `Persistent=true` catches a missed activation when the
Ubuntu user scheduler resumes; it does not wake Windows or start WSL. The first
eligible slot is September 19, 2026 at 02:00 Toronto. An activation record
prevents any earlier period from running during installation.

Each invocation audits the Monday-Friday window preceding the latest due
Saturday. It reads only historical daily prices, existing identity evidence,
the active daily-price selection and its existing completion gate. It requests
FMP full-EOD data only for symbols missing expected sessions. Limits are
3,000 requests, 256 MiB and 60 minutes for acquisition/publication, with one
request per incomplete symbol, zero automatic retries and the existing shared
FMP allowance. The service has a 61-minute hard stop including final audit.

The initial population is pinned by price-population digest to 2,359 existing
symbols (2,248 selected US equities, 96 curated US-listed ETFs, 15 indices).
A changed population requires review before provider work. This is an explicit
operational calendar assignment for this existing stock/ETF roster, not a
general inference that all equity or ETF instruments use US sessions.
Exchange calendars use installed `exchange_calendars==4.13.1`; a changed
version or unavailable coverage fails closed. Its API/source is documented at
https://github.com/gerrymanoim/exchange_calendars .

The index calendar mapping is explicit in the wrapper. EURO STOXX 50 remains
unreviewed and is reported without requests. If listing evidence is absent,
the earliest retained price is only a conservative lower boundary; dates
before it remain unverified, never assumed IPO dates or fabricated prices.
The read-only September 15 preflight found eight such coverage issues:
seven indices with unverified listing boundaries and one unreviewed calendar.
Those records remain actionable gaps. This routine does not promise to repair
unsupported calendars or recover an instrument with no established history.

## Publication and failure rules

The existing selected history parser, original-response queue, provider
credential resolver, shared provider gate and physical-store publisher are reused.
Weekly requests use stricter 64 KiB/five-row transport limits. Full original
response bytes and original source row positions remain intact. Incomplete,
duplicate, wrong-symbol, off-calendar and invalid OHLCV responses do not publish.

The prepared request scope records `missing_price_repair_contract=v1`.
Inside the existing locked SQLite transaction, any current price is skipped
regardless of whether the response differs. No existing price/version pointer
is overwritten. The ordinary history/current publication policy is unchanged.
The repair policy participates in semantic identity; exact replay changes
nothing. New facts retain their actual capture time, preserving as-of behavior.
No schema, migration, ownership or store path changes occur.

A durable weekly start marker is written before any provider request. A
completed period returns its retained result. An interrupted period requires
manual reconciliation and cannot silently restart. The before/after audit,
queue requests, responses, ledger, progress and result live under
`data/.operations/collection/daily-prices/weekly-repair/<Friday>/`.
When offline across multiple weeks, only the latest due week is attempted;
older missing or unresolved weekly receipts are listed without fetching them.
Unresolved prices, identity/calendar problems, exhausted limits and failures
produce a nonzero exit and appear in the existing fetch-status view.
No email, external message or Windows wake configuration is added.

## Validation and activation

The user replaced repository-wide validation with affected-component checks.
The existing 60 focused checks, independent review, and unit/calendar checks
cover the weekly repair boundary; implementation and focused tests are unchanged
from that validated snapshot. No further full-suite run is required for this
task. Full-suite attempts remain interrupted evidence, not passing results.

The user's subsequent go-ahead resumed activation. The timer is enabled and
active (waiting), with its next run September 19, 2026 at 02:00 Toronto / 06:00 UTC.
The service has not executed and no weekly provider request or publication has
occurred. WSL and its user scheduler must be running for an on-time execution.
The checkpoints below preserve the earlier sequence; the latest activation
checkpoint supersedes the prior hold and pending full-suite statements.

Task-specific preimages, logs and read-only preflight evidence:
`.local/weekly-price-repair-20260915/`.

### Completed pre-activation checks

- 60 focused and adjacent tests passed in 30.833 seconds.
- Both new unit files passed systemd-analyze verification; the next occurrence
  resolves to September 19, 2026 at 02:00 EDT / 06:00 UTC.
- Independent read-only fallback review found no blocker and separately ran
  all 12 weekly tests successfully in 11.553 seconds. The configured Astra/xhigh
  verifier failed to start twice with backend HTTP 404. The effective reviewer
  was the project's Sol/xhigh deep-review role, independent of implementation
  and tests. This route difference does not establish deployment evidence.
- Full-suite validation remains running. Two failures already reproduced with
  pre-task source loaded in a separate process: the old analyst registry
  generator expectation omits later transcript tools, and the returns manifest
  expectation omits already-existing version 2.1.0. Neither is changed here.
  The baseline reproduction ran 2 tests, with the same 2 failures and no errors.
- Live timer remains uninstalled at this checkpoint. No provider or canonical
  price write has occurred for this task.

### Validation host interruption and memory bound

The full-suite process ended before its summary during host memory exhaustion.
The kernel recorded an out-of-memory kill at 18:05 UTC-04:00, and the WSL user
scheduler disappeared while the system initialized again. No weekly timer had
been installed or started. This is not a passing full-suite result.

The new weekly service is additionally limited to 2 GiB memory, with a 1.5 GiB
soft pressure threshold and OOMPolicy=stop. A memory failure leaves the durable
weekly start marker and prevents automatic retry; SQLite transactions retain
the existing rollback/replay behavior. A subsequent per-module continuation
was abandoned after the same live-data metadata guard exhausted its memory cap.

### User-directed validation hold and isolated source run

The user chose **keep inactive; continue full validation**. The service and
timer are linked for inspection, but the timer is inactive and not enabled.
The activation guard is prepared with the first eligible September 19 slot;
no weekly service execution, provider request or canonical publication has
occurred. Activation remains on hold.

The validation failure was traced to tests/runtime_data_guard.py repeatedly
inventorying the large live data/ tree, then rendering entire metadata
snapshots on mismatch. Normal scheduled writers also make such comparisons
unstable. Full validation now runs from a hashed temporary source copy with
all working source changes and required site fixtures, no operational data or
project credentials, loopback-only network access, and a 3 GiB process limit.
The command remains python3 -m unittest discover -s tests -t . -v.

Two confirmed pre-existing test expectations were corrected: the historical
analyst generator input is compared byte-for-byte with its reviewed, hash-pinned
2.83 successor, and the returns manifest includes the already-deployed 2.1.0
version while retaining the original default-v1 and deprecation assertions.
No generator, golden, registry, migration or public behavior changed.
All eight targeted correction/fixture checks passed in 15.314 seconds.
Independent follow-up review found no blockers. The required complete run
is still pending; these focused results do not replace it.

Evidence: isolated-source-complete.json, isolated-targeted-result.json,
isolated-targeted.log, validation-corrections-review.json, and the pending
isolated-full-suite.log/isolated-full-suite-result.json under the private
task evidence directory. The incomplete fixture-copy run is retained separately.


### Validation follow-up: compatibility corrections

The timer remains linked and inactive, verified again during validation. No
weekly provider request, service execution, or canonical publication has occurred.

The isolated full command is still running. Its implementation snapshot remains
unchanged. Confirmed failures led to narrow validation corrections across 32 test
files: current registry/catalog expectations, exact manifest inventories, the
reviewed historical analyst-history successor, and the fixture-only constructor's
fixed production-root rejection. The omitted original selection CSV was copied
into supplemental snapshots with its existing pinned SHA-256; its ten tests now
pass. Historical registry pins, migrations, and generated catalog files remain
unchanged.

One additional production compatibility issue was reproduced: the Inspector's
exact current-registry guard still required 2.83.0 while the checked-in registry
is 2.86.0. Only that revision literal was corrected. Exact schema/revision checks
and all store/permission boundaries remain intact. The weekly price publisher and
scheduler implementation have not changed since their focused validation.

The four affected Inspector modules plus the corrected catalog method ran 44
checks: 43 passed, with one obsolete guide-count expectation. That final count
was corrected from 77 to 80, and its separate full documentation-to-manifest
equality check passed. Explicit API compatibility calls for versions 2.7.0 and
2.3.0 still pass.

The separate metadata/fixture group ran 43 checks: 40 passed. Its Inspector and
catalog failures are superseded by the passing affected checks above. The third
failure was an exact current-policy list retaining only 44 of the already-declared
51 policies; its seven existing tail entries were added, and the whole affected
method is being rerun. The corrected 18-test historical-runner module is also
still running, with no failures so far.

Independent review approved the current metadata, catalog inventory, constructor
boundary, exact Inspector guard, complete guide mapping, and ordered policy-tuple
corrections. A bounded diagnostic probe sampled expensive historical registry
reconstruction and stopped at its explicit three-minute diagnostic limit; it is
not passing validation evidence. The original long-running finalization test
passed in the required rerun. The current complete-source full run has not
skipped or terminated any test.

Detailed preimages, immutable snapshot manifests, result files, review records,
and logs remain under .local/weekly-price-repair-20260915/. Final full-suite
coverage reconciliation is pending; this checkpoint does not claim a clean
full-command exit.

### User-directed scoped validation

The user identified the repository-wide validation as excessive and requested
affected-area testing and a project rule update. This newer decision supersedes
the earlier full-validation requirement, while the instruction to keep the
weekly timer inactive remains in force.

The two remaining task-owned test processes were stopped after verifying their
commands and isolated source roots. The full suite and final unrelated policy
rerun both exited by SIGTERM (return code -15); neither is a passing result.
All existing logs, failures, source manifests, and review records are retained.

Only this task's unrelated maintenance edits were restored to their exact
preimages: 32 test files and quant_data/canonical_inspector.py. Their proposed
changes are preserved in unrelated-validation-maintenance.patch, which passed
git apply --check after restoration. Other pre-existing edits were preserved.

The weekly implementation, units, and five focused test modules match the
previously validated source snapshot byte-for-byte. The existing 60 passing
checks in 30.833 seconds and independent weekly review therefore remain the
completion evidence. This prose-only rule update uses targeted documentation
consistency, link, and whitespace checks, without rerunning executable suites.

AGENTS.md, TEST_STRATEGY.md, and FAST_PATH_DEVELOPMENT.md now require an explicit
component impact map and a concrete shared-invariant or current-gate reason for
full-suite escalation. Unrelated failures are separate maintenance unless they
affect the requested outcome or prevent its required evidence. No new test
framework, data repair, provider call, or scheduler activation was introduced.

Evidence: user-directed-validation-stop.json,
unrelated-validation-maintenance.json, focused-scope-preserved.json,
component-validation-rule-update.json, and documentation-checks.json in the
private task evidence directory.


### Saturday schedule activated (current)

The user's subsequent "OK go implement" resumed the previously requested
Saturday 02:00 Toronto activation following the scoped-validation handoff.
Before activation, all 14 implementation/unit/focused-test files matched the
validated snapshot; the installed unit links and first-slot guard matched the
reviewed configuration. Unit syntax verification and calendar resolution passed.
No executable test suite was repeated.

The timer was enabled and started through the existing user scheduler.
Readback confirmed UnitFileState=enabled, ActiveState=active, SubState=waiting,
and NextElapseUSecRealtime=Sat 2026-09-19 02:00:00 EDT. LastTriggerUSec and
the service start timestamps were empty; the service remained inactive.
The first-slot guard is unchanged. No manual provider request or repair ran.

The existing limits, missing-only publication policy, and calendar/listing
coverage limitations remain in force. The laptop must be awake with WSL
running for the job to run at the scheduled time. If missed, the existing
persistent timer catches up when the WSL user scheduler resumes, for the
latest due week only.

Activation evidence: schedule-activation-20260915.json under the task's private
evidence directory. The broader interrupted validation logs remain retained.

### Empty-response isolation and explicit recovery — September 20, 2026

The user requested a fix and one manual recovery of the September 14–18 run.
An HTTP 200 empty JSON array is now recorded as `empty_response` for its
ticker, with the retained response hash and capture time. It leaves the missing
prices unresolved and creates no canonical publication receipt. Processing
continues to the next ticker. The unchanged queue, provider gate and
missing-only publisher still enforce request charges, rate limits, identity,
physical locks and replay; malformed/partial data, provider-wide stops and
uncertain acquisitions retain their stopping behavior.

The wrapper dispatches one unit at a time against its existing shared queue
and invocation deadline. It does not mark empty responses as successful or
fabricate a price. Week-level outcome remains `complete_with_gaps` with a
nonzero exit when any price/calendar/listing problem remains.

An explicitly requested recovery uses:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.weekly_price_repair --recover-week 2026-09-18
```

This command requires the original completed gap receipt and exact Friday
window, restricts requests to symbols missing in its original audit, reuses
the original retained responses, and permits only the remaining unattempted
request allowance. Original charged request/byte totals must reconcile with
the ledger. Missing evidence, an uncertain attempt or an unresolved provider
stop cannot become a new request. A separate `manual-recovery/started.json`
prevents restarting an interrupted recovery; a completed recovery replays its
saved result without provider work. A later additional attempt requires new
authorization and reconciliation, not deletion of the guard.

Original before/after/start/result receipts remain unchanged. Recovery has its
own audit, per-symbol outcomes and result under the original week's
`manual-recovery/` directory. Its request counters describe only new work.
Manual recovery is not recorded as a scheduled timer invocation; the normal
schedule and its original run-history receipt remain intact.

For this authorized recovery, ATAI's original empty response is reused and
only IRBO can generate one new FMP request for September 14–18. There are no
automatic retries, alternate symbols or older-date requests. GOOGL's older
August gap is outside this recovery.

#### Recovery completion

The manual command completed processing on September 20 with
`failure=null` and `outcome=complete_with_gaps`. Exactly one new request was
made for IRBO; FMP returned HTTP 200 with `[]` (two bytes). ATAI's original
September 19 `[]` was replayed without another request. Both outcomes are
recorded explicitly; neither produced a canonical publication receipt or
price. The ten missing sessions (five per ticker) remain gaps, alongside the
eight previously reported calendar/listing issues. The nonzero exit continues
to report those real gaps; it no longer represents the earlier empty-array
validation exception.

All eight original immutable receipt/evidence files remained byte-identical.
The queue contains exactly two cumulative charged requests/four response bytes
and no pending request. Scoped canonical price rows remained unchanged.
The original timer is still enabled and scheduled for September 26 at 02:00
Toronto; its normal future runs load the corrected project code.

Validation: 57 distinct focused tests passed (19 weekly repair, 12 run history,
9 history windows and 17 publication deadlines). Initial fixture-permission
and scheduled-entrypoint compatibility failures were corrected and the
affected checks passed. The independent reviewer inspected the final 31-test
weekly/history run and separately verified eight in-memory guard scenarios,
with no remaining blocker. No full suite was required for this component fix.

Evidence is under `.local/weekly-price-repair-recovery-20260920/`:
`preflight.json`, `focused-final.log`, `focused-final-result.json`,
`manual-recovery.log`, `manual-execution.json` and `completion.json`.
The source preflight preceded the final accounting guard; execution explicitly
verified the independently reviewed final source SHA-256
`8d4dcae830bf0670c972ad2882f3784d1325d10aaccdaf4f00374ff4e1c1d342`.
The execution receipt preserves both hashes.


## Retired-symbol acquisition exclusions — September 22, 2026

At the user's request, future weekly invocations remove ATAI and IRBO before
their coverage audit and provider-request plan when the week ends after their
reviewed last trading dates (September 10, 2026 and August 9, 2024 respectively).
The report records excluded_symbols with the reason and primary source.
This does not change old receipts or histories, repeat completed weeks, remap
IRBO to ARTY, or change timers. See the shared price_fetch_policy module and
the corresponding operating-envelope decision.

## September 26, 2026 — issuer-only startup compatibility repair

The September 26 02:00 Toronto invocation exited with ConflictError before
creating its weekly start marker or making any provider request. The Q/XOM
issuer correction had changed the mapping snapshot ID, while all 2,248 selected
price identities and 111 retained ETF/index entries stayed unchanged.

The weekly startup now reuses the daily-price compatibility proof: reconstruct
the original approval at its recorded cutoff, validate both retained selections,
and permit only CIK and explanatory-note differences. That original selection
must also match the unchanged weekly population/calendar fingerprint.
Membership, price symbols, instrument identities, provider subjects, resolution
status and retained universe snapshots still have to match. The history gate
receives the explicit stores and cutoff and continues to verify all original
completion, quarantine and audit hashes. No shared identity or publisher
contract changed.

Validation: all 60 tests in tests.operations.test_weekly_price_repair,
tests.operations.test_collection_price_quarantine,
tests.operations.test_selected_price_refresh and
tests.operations.test_fetch_run_history passed in 49.022 seconds. The six new
weekly startup regressions cover unchanged and issuer-only selections, actual
price changes, a mismatched weekly pin, altered original receipts, and missing
or future approval cutoffs. The focused boundary did not require a full suite.

A bounded immutable-read preflight passed both the failed run's cutoff and the
current cutoff. Provider access was blocked and collector construction replaced
with an inert stub; the scheduled entrypoint was not called. Original approval
and failed-run receipt hashes, database file stamps and sidecar stamps were
unchanged. Evidence and pre-edit copies are under
.local/weekly-price-gate-20260926/.

The user requested this code update only. No manual retry, provider call,
canonical publication, activation-record rewrite, or scheduler action occurred.
The September 26 failed receipt remains historical evidence.
