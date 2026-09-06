# Inspector source dates and calendar repair — 2026-09-05 Eastern

The user explicitly requested repairing genuine failures, live-testing and
manually fetching affected data, distinguishing legacy history and expected
weekly dates, and separating source as-of dates from successful fetch times.
The finite live scope was one current FMP US calendar request for the existing
2026-08-18 through 2026-11-15 block, with existing FMP_API_KEY resolution and
raw/GDP-CPI/employment publishers against data/macro.sqlite. No retry,
historical-population repeat, new provider, migration, or unit mutation was
included. The manual request is complete and grants no repeat authority.

## Findings and changes

- The normalized calendar stopped on new `Cleveland CPI MoM (Aug)` and
  `CPI n.s.a MoM (Aug)` labels. Cleveland CPI is not a BLS headline/core target;
  NSA month-over-month CPI does not share the accepted seasonally adjusted
  BLS identity. Both are explicitly excluded from normalized target facts and
  retained in wholesale evidence. Other unreviewed target aliases still fail
  closed. Target semantic identity and schema remain unchanged.
- SOMA retains Wednesday 2026-09-02 holdings, captured September 3. This is
  weekly source data despite weekday polling. Petroleum retains week ending
  2026-08-28, from the September 2 release; EIA's announced next release was
  September 10 after the Labor Day holiday. Neither observation date alone
  established a failed source request. The aggregate macro service's earlier
  nonzero exit had no available per-source journal evidence; it was not used
  to claim SOMA or petroleum failed, and the 94-request aggregate was not run.
- The old NIPA GDP history (T10101/T10105) remains a historical snapshot, with
  latest source quarter 2026Q2. Current official GDP vintages are separate.
- Legacy wholesale calendar evidence remains frozen under ADR 0012; the UI
  identifies its active compact incremental successor.

The Data Status HTML view now separates As-of Date, Latest Successful Fetch
(Eastern EST/EDT), timer cadence, and next timer trigger (UTC). Last stored
capture, retained threshold status, outcomes and other metadata remain in
Inspect and the complete no-JavaScript table. Legacy/historical rows are muted
without reducing text opacity. Weekly-source notes explain observation dates;
a conservative source-age caution starts only after fourteen days. No source
as-of date is invented from capture time, and calendar event dates are not
presented as an as-of date. Public data.get_dataset_status@1.0.0 and JSON payloads
remain unchanged.

Private bounded receipts track FMP raw and normalized outcomes and each future
macro-current collector completion, including unchanged successful polls.
FMP successful-fetch time means HTTP response completion. Aggregate collector
time means successful collector completion; unspecified change counts remain
`succeeded`, not falsely classified as unchanged. Failed processing retains
any known successful-fetch marker. Atomic receipt writes use a separate
bounded lock and never cause a provider retry. Older unrecorded polling times
remain Not recorded. No canonical no-op rows are added for this observability.

## Executed live result

The one request completed at 2026-09-06T02:38:44.189005Z
(September 5, 22:38:44 EDT). Successful HTTP response completion was
2026-09-06T02:38:43.924769Z. The response contained 879 raw events.

- Raw incremental publication: one receipt and 68 event versions.
- GDP/CPI publication: four event versions (10 normalized input events).
- Employment publication: four event versions (four normalized input events).
- Normalized event-version count: 1,603 → 1,611.
- Raw event-version count: 1,345 → 1,413; receipt count: 15 → 16.
- Legacy counts remained 62 captures and 47,754 raw rows.
- Targeted foreign-key checks passed before and after for normalized calendar
  versions, compact receipts and raw event versions (zero violations).

Private scope, attempt claim, before-state and result evidence:
`data/.operations/calendar-repairs/2026-09-06-inspector-repair/`.
The attempt claim prevents accidental re-execution of this manual runner.
No second provider request or manual aggregate macro run occurred. Existing
timers were not installed, restarted, enabled, disabled or edited; subsequent
normal clock execution loads the corrected wrapper code.

## Validation

The focused parser/publisher/orchestration and Inspector suite passed 89 tests,
including offline exact-replay and raw-first failure handling. The receipt,
macro observer, timezone, presentation and schedule suite passed 50 tests.
These overlap and are reported as separate runs, not 139 unique tests.
Fixture checks use explicit temporary stores, without credentials/network or
live-store fallback. The full repository suite was not required for this
bounded parser, private operational-observability and HTML-only projection
change; no shared migration, registry, publisher identity or applied schema
was changed. Browser QA passed 53 assertions and nine focused width checks
after a 320px overflow correction. Desktop/mobile screenshots, keyboard
controls, escaped metadata, no-JavaScript fields and local-only assets were
verified. Fixture fingerprints stayed unchanged. Evidence is under
`/tmp/inspector-status-qa-81S0aA2M/` (browser-results.json,
browser-width-results.json, fingerprints.json and final screenshots).

The final integrated source-date, timezone and unchanged JSON-route checks
passed nine tests after the conservative age-window adjustment. The real
Inspector was restarted at port 8766 and its HTTP page verified against the
new successful-fetch receipt and stored SOMA, petroleum and NIPA source dates.

Final receipt validation also rejects inconsistent success markers; twelve
focused receipt/source-metadata checks passed after that boundary correction.
