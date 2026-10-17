# Selected price history completion — September 11, 2026

History settled at 01:33:08 UTC. The reconciled offline validation passed at 05:32:21 UTC, followed by independent acceptance. The expanded seven-day daily refresh activated at **05:41:58 UTC** after a fresh immutable canonical audit. The actual completion gate, current selected scope and enabled timer were verified at 05:42:33 UTC.

## Completed coverage

All **2,248 selected symbols** are mapped to FMP, have canonical prices, and have reached the configured empty-provider-window historical boundary. There are no pending histories. The ten missing identities were resolved with exact profile symbols, existing SEC CIKs and explicit share-class associations; the previous 2,238 mapping entries were preserved.

This completion added **73,015** canonical price rows. Combined with the earlier run, the backfill added **9,783,014** rows through **September 9, 2026**, preserving pre-existing prices. This is completion of the available provider history with documented gaps, not a claim of every exchange session being present.

## Explicit quarantine

Original rejected responses are retained intact. Clean ranges were acquired as separate original provider responses and passed the existing strict publisher. Rejected parent windows were not published as complete captures.

| Provider symbol | Quarantined dates |
| --- | ---: |
| BNY | 3 |
| DFTX | 1 |
| HE | 814 |
| RCAT | 1 |
| SENEB | 412 |

Total: **1,231 dates across five symbols**. BNY includes two additional dates, March 6 and 7, 1974, whose retained rows were left unpublished after their response violated request-date bounds. Their OHLC values were not declared invalid or repaired. No timezone cause is asserted. The other 2,243 histories completed without quarantine. A wholly quarantined nonempty response never served as an empty historical boundary.

## Completed holdings and retained date ranges

| Source symbol | FMP symbol | First stored date | Last stored date | Stored rows |
| --- | --- | --- | --- | ---: |
| BNY | BNY | 1969-01-02 | 2026-09-09 | 14,298 |
| DFTX | DFTX | 2016-11-15 | 2026-09-09 | 2,465 |
| HE | HE | 1973-02-21 | 2026-09-09 | 12,687 |
| RCAT | RCAT | 2002-01-16 | 2026-09-09 | 6,198 |
| SENEB | SENEB | 1980-03-17 | 2026-09-09 | 11,247 |
| ARWR | ARWR | 1993-12-16 | 2026-09-09 | 8,237 |
| AUR | AUR | 2021-05-10 | 2026-09-09 | 1,340 |
| BF.A | BF-A | 1968-01-04 | 2026-09-09 | 13,808 |
| BH.A | BH-A | 2018-05-01 | 2026-09-09 | 2,080 |
| GEF.B | GEF-B | 1996-03-06 | 2026-09-09 | 6,723 |
| HEI.A | HEI-A | 1998-04-09 | 2026-09-09 | 7,140 |
| INTR | INTR | 2022-06-23 | 2026-09-09 | 1,057 |
| LEN.B | LEN-B | 2003-04-09 | 2026-09-09 | 5,890 |
| MOG.A | MOG-A | 1980-05-29 | 2026-09-09 | 11,664 |
| UHAL.B | UHAL-B | 2022-11-10 | 2026-09-09 | 959 |

## Workload and validation

The completion used **10 profile GETs and 988 history GETs**, receiving **17,153,094 bytes**. The user-approved 200-request extension for BF.A and MOG.A used eight requests. The original one-hour and 1 GiB completion bounds were retained. There were no automatic retries. All prior-run charges and the 335,544,320-byte uncertainty reserve remain preserved.

Fifty-five focused and adjacent checks passed. Five isolated continuation checks and four query-equivalence cases also passed. A read-only audit initially reached its 90-second query limit; an equivalent covering-index aggregate passed without changing store or index semantics. Independent source review cleared acquisition, settlement, and the conditional activation design.

An initial full-suite run was interrupted during concurrent authorized data operations. The two failed macro guard cases passed an isolated recheck; the guard source and overlapping data writes support the interference explanation. Original failure stack traces were not captured before that interruption.

The subsequent physical-copy run recorded 1,319 unique passing cases, seven errors and one unfinished case before stopping when a legacy HTTP fixture reached the hardcoded host allowance path. Its raw exit remains -15. The fixture's request identity and recorded `not_dispatched` state proved that its transport callback was never entered. Only the new stop flag was cleared under the existing account lock after independent review; the charged unit and all earlier evidence remain retained. A separate earlier fixture incident repaired by the company task was not repeated.

The required suite passed through unique-case reconciliation under TEST_STRATEGY section 4: **2,166 distinct cases**, no missing cases, no unresolved failures and no skipped cases counted as passes. The frozen 2,159-case integration comprises 1,219 reused proven passes, completed prefixes of 581, 36 and five cases, a 302-case group, and completed groups of six, five and five cases. Four duplicate discovery IDs refer to the same imported test class and are counted once.

The final current-configuration run passed 52 checks in 56.582 seconds, including seven added FMP date/continuation/locking/window cases. Two existing generator tests initially tried to create a lock under the read-only source. Their exact three registry/catalog resources now copy to explicit temporary roots; the real generator, lock and checksum assertions remain in use. Their separate rechecks passed in 1.220 and 1.491 seconds. The original 302-case group's exit 1 and both failures remain recorded and explicitly resolved.

The isolated environment masks the canonical project with a read-only physical source copy, uses private empty homes and loopback-only network namespaces, and provides unique temporary store/work roots. Three prefix processes were deliberately terminated only after their assigned cases and fixture teardown completed and the next independently covered module had begun. Their observed -15 statuses, duplicate passes and unfinished duplicate cases remain retained; they are not reported as clean full-suite exits.

Independent review checked the exact case partitions, source and evidence hashes, teardown boundaries, fixture corrections and price compatibility. The separate Equibles parallel, current-news and Inspector changes are outside this price validation claim. Price-imported Equibles filesystem/lock helpers and import-time behavior matched the frozen baseline. The complete case-by-case proof is in data/.operations/collection/price-completion-20260911/resumed-full-suite-validation.json.

## Daily refresh

The selected daily route passed a zero-GET preflight for **2,248 equities plus 111 retained ETF/index instruments**, totaling 2,359 deduplicated requests. Its ordinary window remains the latest **seven calendar days**. The expanded route is **active** with a seven-calendar-day window. The existing timer is enabled and scheduled for weekdays at **18:00 America/New_York**, with the next run September 11 at 18:00 EDT. The first expanded clock invocation has not yet been observed. Activation used zero provider GETs and no manual fetch, timer installation or restart.

The initial activation audit reached its 90-second progress limit and left completion and activation markers absent. A bounded query-plan check confirmed the existing covering index; one rerun of the unchanged driver completed the fresh audit and activation in 6.149 seconds. The original timeout is retained. No query, index, canonical data, provider allocation or validation source was changed for this rerun. The quality and canonical-audit receipts were written first, followed by history completion and activation last. The actual gate then passed against the current 2,248-member mapping plus 111 retained instruments.

## Evidence

- `data/.operations/collection/price-completion-20260911/scope.json`
- `data/.operations/collection/price-completion-20260911/mapping-activation.json`
- `data/.operations/collection/price-completion-20260911/history/settlement.json`
- `data/.operations/collection/price-completion-20260911/history/settled-canonical-audit.json`
- `data/.operations/collection/price-completion-20260911/history/publication-result.json`
- `data/.operations/collection/price-completion-20260911/extension/authorization.json`
- `.local/universe-rollout-20260909/isolated-price-validation-manifest.json`
- `.local/universe-rollout-20260909/network-isolated-price-environment.json`
- `.local/universe-rollout-20260909/interrupted-full-suite-case-events.json`
- `.local/universe-rollout-20260909/resumed-price-validation-manifest.json`
- `data/.operations/collection/price-completion-20260911/offline-allowance-reconciliation-result.json`
- `data/.operations/collection/price-completion-20260911/concurrent-config-delta.json`

- data/.operations/collection/price-completion-20260911/resumed-full-suite-validation.json
- .local/universe-rollout-20260909/final-price-focused-result.json
- .local/universe-rollout-20260909/price-fixture-validation-result.json
- .local/universe-rollout-20260909/price-version-fixture-result.json
- .local/universe-rollout-20260909/resumed-price-prefix-stop.json
- .local/universe-rollout-20260909/auxiliary-prefix-stop.json
- .local/universe-rollout-20260909/last-stage-prefix-stop.json
- .local/universe-rollout-20260909/parallel-stage7-result.json
- .local/universe-rollout-20260909/parallel-stage8-result.json
- .local/universe-rollout-20260909/parallel-stage9-result.json

## Final activation evidence

- `data/.operations/collection/price-completion-20260911/activation-validation.json`
- `data/.operations/collection/price-completion-20260911/activation-audit-timeout.json`
- `data/.operations/collection/price-completion-20260911/activation-execution.json`
- `data/.operations/collection/price-completion-20260911/activation-execution.log`
- `data/.operations/collection/price-completion-20260911/completion-and-activation.json`
- `data/.operations/collection/price-completion-20260911/activation-verification.json`
- `data/.operations/collection/price-completion-20260911/history/canonical-audit.json`
- `data/.operations/collection/daily-prices/history-canonical-audit.json`
- `data/.operations/collection/daily-prices/history-completion.json`
- `data/.operations/collection/daily-prices/history-quarantine.json`
- `data/.operations/collection/daily-prices/live-activation.json`

The accepted unique-case validation receipt SHA-256 is `15873bc23c1d5264bd5f9316d4fb8dcb0614a966228c565da2d5ca22a8de2930`. The live population SHA-256 is `ac3d7d7bdd20cb013119e955e1592b146391eb581496621d4e57dfe6825570c3`.

## Changed implementation

Price quarantine validation is in `quant_data/operations/collection_price_quarantine.py`; the selected daily route checks its versioned completion gate in `quant_data/operations/selected_price_refresh.py`. Boundary tests are in `tests/operations/test_collection_price_quarantine.py`. Offline fixture isolation corrections affect `tests/operations/test_fmp_macro_calendar_history.py`, `tests/tool_platform/test_catalog_dispatch.py` and `tests/tool_platform/test_market_return_versioning.py`. The dated private completion, continuation and extension drivers and all provider evidence remain in their existing operational roots. Unrelated concurrent changes are preserved; no commit or push was made.
