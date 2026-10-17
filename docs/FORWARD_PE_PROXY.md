# Daily forward P/E research proxy

Contract: `announcement_forward_pe.v1`. Manual local research artifact.
This implements the user's September 19, 2026 decision to reconstruct forward
estimates using actual earnings announcements and merge them with daily prices.
It is **not point-in-time consensus history**.

Run from the project in Ubuntu/WSL:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.forward_pe --allow-unverified-basis
```

The command defaults to all retained quarterly-estimate tickers, from 1980 through
today, limited to each security's retained price start. To select a smaller slice:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.forward_pe --symbols AAPL MSFT --start 2016-01-01 --end 2026-09-18 --allow-unverified-basis
```

Omit `--allow-unverified-basis` for strict mode: P/E stays null unless currency,
EPS accounting basis, and a common share/split basis are established. In the
research mode, an otherwise computable ratio is explicitly marked
`unverified_basis` (or `expected_loss` for negative EPS, retaining the unverified
basis flag); this does not establish currency or split comparability.
Known currency, accounting, share-basis or adjustment conflicts always block P/E.
The current retained EPS inputs generally lack explicit share-basis metadata.

## Calculation

- Select each estimate's latest retained version at the build cutoff.
- Match actual reported earnings to retained statement period ends within
  120 days. An explicit period must exist in those statements; otherwise a
  unique exact positive revenue match takes precedence over transcript labels.
  Conflicting transcript labels remain flagged. Repeated exact revenues can
  be disambiguated only when one matched period also has a uniquely matching
  filing dated 0–7 days after the earnings event.
- Transcript fiscal labels may use either provider's retained fiscal-year
  convention for the same actual period end. Read Sharadar's retained
  `fiscalperiod` as a label alias, without rewriting source records or dates.
  Same/next-day calls retain priority. A unique nearby call within ±14 days
  may corroborate a period only when it is associated with this reported
  earnings event and no other reported event in that interval. The method and
  call-date disagreement are retained in the window's evidence and flags.
- If a same/next-day call's labels cannot be linked, a unique statement filing
  within 0–7 days may corroborate the actual period. Filing dates support
  period identity only: they never replace the actual announcement date or
  advance the effective session. Conflicting candidates and missing evidence
  remain explicit gaps. Policy: `corroborated_fiscal_periods.v2`.
- Income statements supply actual period ends. Retained Sharadar ARQ rows
  supplement FMP, using the existing pinned cross-provider collection mapping.
  Partial issuer/security linkage remains flagged; it is not upgraded to
  complete identity evidence. Filing dates never substitute for announcements.
- Provider estimate target dates may differ from actual statement ends.
  Reconcile date clusters wholly within seven days, with at most one actual
  period end. Revisions that change the target date use the unique newest
  capture at the existing cutoff; retained component evidence lists every
  aliased date/version. Same-capture collisions, ambiguous statement ends and
  chains spanning more than seven days are not silently resolved. This also
  reconciles revised dates for future periods without an actual statement yet.
  Policy: `latest_capture_unique_fiscal_period.v1`.
- Following announcement of period q, sum mean EPS for q+1 through q+4.
  Require all four estimates and consecutive period ends normally 70–110 days
  apart. A 111–119-day quarter is allowed only when the issuer's retained
  consecutive fiscal labels demonstrate that long-quarter calendar; this is
  flagged `observed_long_fiscal_quarter`. Missing full quarters and unsupported
  stub periods remain blocked. Actual fiscal period ends determine ordering,
  not standard calendar quarters or one assumed fiscal-year naming convention.
- Only reported earnings (actual EPS or revenue present) roll the window.
  Scheduled events do not. A duplicate or older-period amendment cannot roll
  the window backwards.
- A confirmed release strictly before the exchange close takes effect for that
  close. At/after-close releases apply next session. Date-only releases apply
  next session with an explicit timing assumption; no timestamp is invented.
- Use the installed `exchange_calendars` XNYS cash-equity session schedule,
  including holidays, early closes and DST. This profile is for US listings;
  missing venue metadata is flagged as an assumed US listing calendar.
  Explicit unsupported venues are blocked. The exact schedule is retained.
- Left join to those daily sessions without filling missing prices. Daily P/E
  is close divided by the selected forward EPS. Under
  `signed_nonzero_forward_eps.v1`, negative EPS yields a negative ratio labelled
  `expected_loss`, with `negative_forward_eps` in its flags. It does not override
  other blockers or certify basis comparability. Zero EPS keeps a null ratio
  with `zero_forward_eps`; missing inputs remain separately unavailable.
  No interpolation or estimate-revision reconstruction is performed.
- Use the existing capture-validated FMP full-EOD close binding (split-only,
  excluding distributions). Do not use dividend-adjusted prices or silently
  adjust EPS. Mixed capture/share-basis uncertainty is disclosed.

This is a four-quarter *unreported-period* proxy, not exactly twelve calendar
months from every observation date. Historical changes to the frozen EPS series
come from window rolls; they do not measure historical analyst revisions.
Source availability remains the actual capture date, not the historical chart date.

## Output

Each invocation creates an immutable, timestamped SQLite file under
`exports/forward-pe/`. Existing builds and all operational stores are preserved.

- `daily_forward_pe`: convenient view with symbol, date, close, forward EPS,
  forward P/E, status, announcement, reported period end, components and flags.
- `daily`: one row per security/session, including explicit missing results.
- `windows`: announcement boundaries, four components and their source IDs.
- `source_inputs`: compressed exact selected source inputs and SHA-256 hashes;
  decode the BLOB with Python `zlib.decompress` and `json.loads`.
- `metadata`: build cutoff, method/version, code hashes, exact session calendar,
  per-security source fingerprints, coverage summary and assumptions.

For example, in the **derived database only**:

```sql
SELECT trade_date, close, forward_eps, forward_pe_proxy, status
FROM daily_forward_pe
WHERE symbol = 'AAPL'
ORDER BY trade_date;
```

Source reads use coordinated physical company/market locks and the approved quiet
immutable reader. Coherence is per instrument, not an asserted globally atomic
snapshot across the entire run. Every component uses the same build cutoff.
The builder uses no credentials, provider calls, canonical writes, migrations,
recurring units or public-tool contract changes. The local dashboard below
reads its completed output.

Bounds: 2,500 symbols, one metadata lookup capped at 1,000,000 ARQ keys,
30,000 rows per source query (5,000 Sharadar rows per
instrument), 30 seconds per source-read cohort, one hour per build and an 8 GiB
output guard checked after each instrument. Exceeded bounds fail explicitly,
without silently truncating. Incomplete output retains a `.partial.sqlite`
suffix; completed output is published without overwriting an existing file.

## Validation

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.company.test_forward_pe tests.company.test_forward_pe_period_reconciliation tests.dashboard.test_forward_pe_page -v
```

Coverage includes non-calendar fiscal years, announcement timing, exchange
holidays and early closes, 53-week periods, missing quarters/prices, ambiguity,
negative EPS, build cutoffs, basis conflicts and opt-in flags, immutable source
reads using temporary stores, and artifact integrity/reproducibility.

## Completed retained-data build — September 20, 2026 UTC

The build at cutoff `2026-09-20T03:52:40.119234Z` processed 2,182 tickers:
12,636,373 daily rows, including 6,428,083 numeric research P/E observations
across 1,936 tickers. Another 246 tickers have no numeric P/E under these rules.
All numeric ratios carry `unverified_basis`; they are not certified as
currency/share/split comparable. Historical coverage varies with retained
statements, announcements, estimates and prices.

Artifact: `exports/forward-pe/20260920T035241048695Z.sqlite` (6,058,237,952 bytes).
SQLite integrity and foreign-key checks passed, along with eight arithmetic
spot checks against exact source inputs. The adjacent `.manifest.json`
contains coverage, validation and both implementation hashes. It supplements
the initial artifact's basename-keyed code hash; subsequent builds use full
relative source paths. The database itself was not rewritten.

The 26 focused tests passed. Private source snapshots and the completion
receipt are retained under `.local/forward-pe-20260920/`.

## Dashboard

Open `http://127.0.0.1:8766/forward-pe`, or choose **Market → Forward P/E**.
Select a ticker and one year, five years or all history. The chart can show
P/E, forward EPS or closing price. Clicking the chart or moving its
keyboard-accessible session slider shows that day's four fiscal period ends,
EPS components, announcement and flags. Missing values break the line; the
P/E line also breaks whenever forward EPS changes sign. Negative ratios are
labelled **Expected loss**, zero EPS **Undefined — zero EPS**, and missing
ratios **Data unavailable**, retaining the specific reason. Coverage counts
available ratios, negative ratios, and any undefined/unavailable sessions
separately. Negative P/E is not comparable with positive valuation multiples.
The most recent session stays the headline even when its P/E is unavailable;
an older valid ratio is never silently substituted. Recent daily observations
and the latest window remain readable without JavaScript.

The host selects a completed immutable artifact through
`exports/forward-pe/current.json`:

```json
{"artifact": "20260920T035241048695Z.sqlite"}
```

The pointer is explicit: a later pilot or partial build cannot become the
dashboard's dataset merely by having a newer filename. The reader accepts
only timestamped SQLite basenames inside the fixed export directory, refuses
symlinked artifacts and active journals, and opens with
`mode=ro&immutable=1` plus query-only mode. Browser filters are limited to
ticker and a fixed range; no caller-controlled file paths, SQL, provider calls,
canonical-store reads or writes. Reads are bounded to 2,500 tickers, 30,000
daily rows and five seconds of SQLite work. Only referenced window details
are loaded; compressed source evidence is not expanded.

This is currently a **manual snapshot**. The page displays the snapshot cutoff
and latest saved price date, and explicitly states that daily refresh is not
active. The initial selected snapshot is the completed build above.
Dashboard validation: 20 distinct focused tests passed across
`tests.dashboard.test_forward_pe_page`, `tests.test_inspector_presentation`
and `tests.test_transcript_extraction_routes`. Chromium checks covered
desktop (1440 px), mobile (390 px), chart/slider interaction, ticker and range
selection, all-missing P/E with available prices, and the no-JavaScript table.
Desktop and mobile screenshots were visually reviewed. No full-suite run was
required for this additive local presentation/derived-reader change.


## Proposed daily pipeline — not activated

Keep the existing collectors and operational stores as they are. Add one
derived research refresh after their normal runs; it must not invoke collectors
or spend additional provider units.

1. **Wait for retained inputs.** Repository timer declarations place market
   close at 18:00 New York, company/actions at 19:00 and selected company inputs
   (including quarterly estimates and earnings) at 20:00 on weekdays. Those
   declarations are not proof of live unit state. Use the completion receipts
   of the relevant price and selected-company runs, not a presumed 20:05 finish.
   Run once per new US trading session after both have completed. If company
   inputs are late, a price-only update may use the last saved denominator,
   explicitly labelled with its estimate cutoff; it must not claim fresh consensus.
2. **Update incrementally.** Reuse the existing announcement/window calculation
   for each selected ticker. Add new sessions and check the last five sessions
   for corrected prices or delayed earnings; a flagged older correction requires
   a separate bounded repair. Roll the four-quarter window on actual announcements.
   Refresh its EPS when a new retained estimate version is available. Keep missing
   values, nonpositive EPS and basis warnings exactly as in the initial build.
3. **Preserve the historical convention.** Freeze the September 20 reconstructed
   baseline. From activation forward, each daily value records the actual source
   versions and cutoff used that day. New consensus captures affect new observations,
   rather than rewriting the entire back history each night. Distinguish these
   daily-capture observations from the reconstructed baseline in metadata and UI;
   they do not retroactively establish true historical point-in-time coverage.
   A later historical reconstruction is a separately named research revision.
4. **Validate, then publish.** Use a separate derived working file, the existing
   physical-source locks for bounded reads, and one writer for this output. Save
   source/version lineage for changed rows; validate four components, arithmetic,
   row uniqueness, date bounds and missingness. Publish a completed immutable
   serving snapshot and atomically replace the host-owned current pointer.
   Keep the previous good snapshot available on failure. The incremental runner
   and smaller serving format are follow-up implementation, not capabilities of
   the current full-history command.
5. **Show freshness and bound retention.** Report last price session, estimate
   cutoff, successful refresh time and failure/stale state. Expect one update
   per trading day, not weekends or holidays, with no intraday claim. The next
   normal run can catch up missing sessions within its explicit cap; no hidden
   provider retries. Retain the pinned initial baseline, current serving snapshot
   and previous good snapshot, plus compact receipts; approve the exact retention
   cleanup policy when activating the pipeline. Avoid retaining a new 6 GB
   full-history/source-evidence copy every day.

This is the recommended follow-up design. No timer, service, recurring
automation, collector change or automatic cleanup was installed or activated.
The existing full-history builder remains a manual reconstruction command;
scheduling it unchanged would revise historical estimates and accumulate large
files, so it is not the proposed daily path.


## Targeted price refresh — September 20, 2026 Toronto / September 21 UTC

The historical fill updated canonical prices but did not alter the immutable
P/E snapshot. Browser refresh (including hard refresh) reloads the selected
artifact; it cannot rebuild derived prices. The user subsequently requested
that the corrected prices appear in the GOOGL chart.

The new snapshot `20260921T012728147712Z.sqlite` is a price-only revision of
`20260920T035241048695Z.sqlite`. It incorporates all 6,074 repaired price dates
across 514 tickers while preserving every existing EPS value, announcement
window and unrelated daily row. Exact new source-price versions are included
in the compressed source inputs and their updated hashes. Metadata preserves
the original estimate cutoff and identifies the parent snapshot and targeted
price refresh; the original artifact remains unchanged. No fresh consensus
capture, canonical write, provider request or recurring activation occurred.

The current pointer selects this completed revision. GOOGL's five-year page
now serves **1,255 of 1,255 sessions with P/E**, confirmed through the actual
HTTP page and dashboard reader. AAPL and NVDA also return 1,255 of 1,255.
The revision contains 6,433,866 numeric P/E observations (5,783 more); some
restored prices still cannot produce P/E under the preserved estimate rules.

Validation: three isolated merge checks and 31 calculation/dashboard-reader
checks passed. SQLite integrity and foreign-key checks passed; full snapshot
comparison verified unchanged EPS, window references, windows, ticker count,
row count and all daily observations outside the repaired dates. Browser UI
automation was unavailable because its Windows sandbox failed to initialize;
HTTP validation returned 200 with the expected rendered count and no-store.
Evidence and the one-time driver are in
`.local/forward-pe-price-refresh-20260920/`; the artifact has an adjacent
`.manifest.json` completion receipt. This remains a manual snapshot workflow.


## Quarter-label precedence correction — September 20 Toronto / September 21 UTC

The user approved correcting announcement/quarter matching after MU's saved
transcript labels disagreed with actual statement periods. A unique exact
positive reported-revenue match within the existing 120-day announcement bound
now wins over transcript fiscal labels. Explicit period validation remains
first; ambiguous revenue matches are still blocked and cannot be narrowed by
a transcript label. A resolved disagreement retains
`transcript_fiscal_label_conflict` in the window flags and
`reported_revenue_match_transcript_conflict` as its mapping method. Future
builds record `unique_reported_revenue_precedes_transcript_labels.v1`.

The corrected immutable snapshot is `20260921T015229883870Z.sqlite`, now
selected by current.json. It recalculated 335,883 daily rows for the 40 tickers
with prior quarter conflicts using byte-identical saved inputs. No prices,
estimates, source availability timestamps or canonical stores were changed.
The prior price repair remains included. The snapshot has 6,443,442 numeric
P/E observations, an increase of 9,576 under the corrected mapping.

MU now serves **943 of 1,255** five-year sessions with P/E; the remaining
**312** have nonpositive forward EPS. All 1,255 MU prices are present. GOOGL
continues to serve 1,255/1,255. Both counts were verified through the actual
HTTP page and dashboard reader; disagreement warnings are retained in MU's
window details. Browser UI automation was not available in this session.

Thirty-six focused calculation/dashboard tests passed, including five new
cases for mislabeled fiscal quarters, ambiguous revenue, duplicate provider
rows for one period, explicit period precedence and the announcement bound.
Full artifact integrity and foreign-key checks passed. Source-input equality,
affected-history price equality, row counts and unchanged GOOGL/AAPL/NVDA
histories were verified before the atomic pointer switch. Evidence is in
`.local/forward-pe-quarter-match-20260920/` and the adjacent artifact manifest.

The first staging assembly was deliberately interrupted because parent-row
foreign-key checks repeatedly scanned the full daily table. The completed
40-ticker calculation and unpublished staging copy were reused; the private
bulk replacement deferred constraint enforcement until the mandatory full
foreign-key check before publication. Its successful continuation took
455.941 seconds within a 600-second bound. No failed or partial artifact was
served, no scheduler changed, and no provider request was made.


## Signed P/E display — September 21, 2026 UTC

The user approved showing negative ratios with **Expected loss**, separating
zero EPS from missing data, and breaking the P/E chart on EPS sign changes.
The derived ratio policy is now `signed_nonzero_forward_eps.v1`.

Artifact `exports/forward-pe/20260921T033230486298Z.sqlite` applies this policy to
946 affected tickers using only the saved inputs. It adds 746,102 negative
ratios and separately labels 1,840 zero-EPS daily observations. The complete
snapshot retains 12,636,373 rows, with 7,189,544 numeric ratios. Existing positive
ratios, prices, estimate inputs, source cutoffs and source evidence are preserved.
MU's five-year display now has **1,255/1,255** ratios, including **312 negative**
observations; GOOGL, AAPL and NVDA retain full five-year coverage.

Full SQLite integrity and foreign-key checks passed before the dashboard
pointer changed. Thirty-nine focused calculation/reader/dashboard tests passed.
Local Chromium verified the live MU page, sign-change discontinuities, isolated
points, EPS/price continuity, selected-day labels, desktop/mobile layouts and
negative/zero labels without JavaScript. Screenshots were visually reviewed.
The manual Inspector was restarted to load the new renderer and cached assets.
No provider requests, canonical reads/writes or recurring-unit changes occurred.
Receipts, task preimages and browser evidence: `.local/forward-pe-signed-20260920/`.


## Corroborated quarter reconciliation — September 21, 2026 UTC

After the all-ticker audit, the user approved fixing the different retained
quarter-matching cases. The research matcher now recognizes provider fiscal-year
aliases for the same actual statement end, narrowly reconciles transcript dates
against a unique reported event, and uses a unique nearby filing to corroborate
otherwise conflicting quarter evidence. It normalizes small estimate-date
revisions before choosing the unique newest retained capture, and recognizes
111–119-day fiscal quarters only where the issuer's consecutive statement
history supports them. Actual earnings announcements remain the time anchor;
filing and transcript dates do not replace them. Ambiguous cases stay flagged.
The matching policies are `corroborated_fiscal_periods.v2` and
`latest_capture_unique_fiscal_period.v1`; signed nonzero P/E remains enabled.

The finite saved-input refresh covered all **2,182 tickers**, preserving all
**12,636,373 daily rows**, source inputs, prices, estimate cutoffs and every
previously numeric P/E. It added **54,438 numeric observations across 169
tickers** over all history, bringing the total to **7,243,982**. In the five-year
view (September 20, 2021–September 18, 2026), **128 tickers gained 22,123 values**:
coverage rose from 2,254,301 to **2,276,424 of 2,565,914** sessions.

| Ticker | Five-year ratios before | After |
| --- | ---: | ---: |
| C | 1,127 | 1,255 |
| ADBE | 999 | 1,255 |
| COST | 246 | 1,255 |
| KR | 0 | 1,058 |
| LOW | 1,071 | 1,137 |
| CHWY | 1,136 | 1,207 |

Each of these tickers has 1,255 price sessions. COIN, HOOD, EBAY, NDAQ, TEL
and RH also now have full five-year ratio coverage. MU remains 1,255/1,255
including 312 negative observations; GOOGL, AAPL and NVDA remain complete.

**289,490 five-year sessions still have no ratio**: 197,584 unmapped announcements,
76,335 missing estimates, 9,251 without an announcement anchor, 2,356 missing
quarters, 2,224 nonconsecutive quarters, 1,668 missing prices, 63 conflicting
announcement periods and nine zero-EPS observations. These categories describe
the remaining evidence or arithmetic limitation, not necessarily a new data
acquisition need. LOW and CHWY retain 118 and 48 unsupported announcement
sessions; KR retains 197 nonconsecutive-quarter sessions because some estimate
target dates differ by eight days, outside the supported seven-day alias bound.
No arbitrary quarter inference or source overwriting was used to remove these.

The completed immutable artifact is
`exports/forward-pe/20260921T041832814628Z.sqlite`. Its parent remains available,
and the dashboard pointer switched atomically only after full SQLite integrity,
foreign-key, source-input byte-equality, metadata and targeted arithmetic checks
passed. Computation took 809.849 seconds within its 1,800-second bound; validation
took 111.962 seconds within 900 seconds. Fifty focused calculation/reader/UI
tests passed, including 11 new reconciliation regressions. The actual Chromium
dashboard verified C, ADBE, MU, GOOGL and KR coverage, selected-day provenance and
four components, unchanged MU negatives, and no JavaScript errors. C desktop and
mobile screenshots were visually reviewed with no horizontal overflow.

Zero provider requests, canonical reads/writes, service restarts or recurring
changes occurred. A transient WSL command-connection timeout did not stop
validation or publication; the existing log confirmed successful completion,
and the Linux browser check subsequently completed normally. This remains a
manual, reconstructed research snapshot, not historical point-in-time consensus.
Task preimages, test log, before/after results, execution source, review receipt,
validation, completion and browser evidence are retained in
`.local/forward-pe-link-fix-20260921/`; the artifact also has an adjacent manifest.


## Daily maintenance — September 21, 2026

The approved derived refresh adds a compact SQLite overlay to the pinned
historical artifact. The dashboard reads their merged series. It appends up to
20 completed sessions per ticker per run and compares all bounded saved price
version identities, so late-published corrections reach their exact historical
dates. Existing denominators and original estimate cutoffs remain unchanged.
No provider calls or canonical writes occur.

New ratios reuse the existing announcement/fiscal-quarter matching rules.
Quarterly estimate and earnings capture times are recorded per ticker.
Unavailable or ambiguous identities are isolated. Old inputs stay visibly
stale; new ratios are unavailable when their first forward quarter ended more
than 120 days earlier. These daily captures do not claim market-close
point-in-time consensus. Negative P/E and undefined zero-EPS semantics remain.

The fixed runner is quant_data.operations.derived_refresh. It uses coordinated
immutable company/market reads, one export writer lock, 2,500-ticker and
45-minute bounds, and a 512-MiB compact-output cap. Completion evidence and
SQLite validation precede the atomic serving pointer. The historical baseline,
current and previous compact publications remain available; only redundant
runner-created compact copies are eligible for removal.

The schedule is 23:30 weekdays and 06:30 daily, America/Toronto. Forward P/E
shows the configured cadence and per-ticker input notes; Status includes derived
coverage, latest publication and attention counts. A following-morning run
checks source freshness, appends any unprocessed sessions and repairs prices;
it does not replace a previously published day's EPS with newer consensus.

The implementation and finite initial-run bounds are owned by
[the derived refresh plan](DERIVED_REFRESH_PLAN.md). Dated activation and actual
run evidence belong in the operating envelope.


## Local agent access — September 21, 2026

company.get_forward_pe@1.0.0 exposes the same selected baseline/overlay through
the existing local tool boundary and loopback Inspector Agent Tools page.
It accepts ticker, start_date and end_date (inclusive, maximum 3,661 calendar
days), and returns bounded summary, daily and distinct fiscal-window records.
This is read-only research-artifact access: no canonical store, provider,
scheduler or source-data changes. Negative ratios, null reasons, original
denominators, price/source versions and capture cutoffs are retained.

Registry 2.88.0/catalog 2.34.0 add this one name and its input/output pair.
Exact predecessor projection restores registry 2.87.0. All existing tools,
version variants, datasets, migrations and frozen v1 schemas remain unchanged.
See [Local Agent Tools](LOCAL_AGENT_TOOLS.md#saved-forward-pe-and-forward-eps)
for the complete request and record contract.

Validation: all 54 focused tests passed across the tool reader, catalog,
local launcher and Inspector suites, with corrected cases rerun after fixes.
Generated-output and final diff checks passed; frozen v1 schemas are byte
unchanged. The launcher was called from outside this project for MU and C:
each returned 1,255 daily observations, including MU's 312 negative ratios
and C's stale-input warning. Chromium executed the registered tool with HTTP
200, no JavaScript errors, and a visually checked result page. Evidence and
preimages are retained in `.local/forward-pe-agent-tools/`.

The existing manually launched Inspector was reloaded to advertise registry
2.88.0. No recurring units, provider operations or canonical data were changed.
The full offline suite was not run for this additive reader contract.


## Rolling standardization — September 21, 2026

The user selected a three-year default: 756 saved trading sessions, including
the selected day. The minimum is 252 usable P/E observations. Each session's
2.5th and 97.5th percentile bounds use linear type-7 interpolation over that
trailing window. Both the current observation and window sample are capped
before computing population mean and standard deviation. The z-score is the
capped value minus that mean, divided by that standard deviation. An uncapped
z-score is returned for comparison. Raw P/E is never modified.

Missing values occupy sessions but are excluded from quantiles and moments.
Missing current P/E, insufficient observations and zero variance retain null
scores with explicit reasons. Earlier saved sessions warm up the calculation;
changing the display range preserves overlapping scores. Negative P/E stays
in the signed sample, with an explicit warning about earnings-sign changes.
This is a descriptive transformation of the reconstructed proxy, not a
historical point-in-time valuation signal.

`company.get_forward_pe_analysis@1.0.0` owns the public contract. The domain
reader and calculation are in `quant_data/company/forward_pe_reader.py` and
`quant_data/company/forward_pe_analysis.py`; the adapter is
`quant_data/tool_platform/forward_pe_analysis_access.py`. The dashboard calls
this registered tool and renders its values. It has synchronized raw/capped
P/E and z-score charts, window/tail controls, daily clipping/sample details and
recent raw/capped/z-score columns. No formula runs in the browser. Compact
columnar chunks preserve full histories within the existing response bounds.
See the [consumer contract](LOCAL_AGENT_TOOLS.md#rolling-forward-pe-analysis).

Registry 2.89.0/catalog 2.35.0 add one tool; the exact 2.88.0 predecessor and
all existing contracts, including raw `company.get_forward_pe@1.0.0`, are
preserved. The analysis is computed on demand over the current immutable
publication. Existing daily refresh, canonical storage and provider workflows
are unchanged.

Validation: 54 distinct focused tests passed across arithmetic, raw-reader
regressions, analysis contracts, catalog/launcher compatibility, dashboard and
Inspector discovery. Corrected cases were rerun after fixes; duplicate runs
are not counted. Generated-output, exact predecessor, frozen-schema and diff
checks passed. The launcher returned MU's 1,255 five-year ratios and 1,255
scores, and C's complete 11,774 saved sessions. Chromium verified exact tool/UI
values, initial chart width, both charts, clipping inspection, alternate
settings, keyboard control, no mobile overflow and full-history rendering.
Desktop/mobile screenshots were visually reviewed. Initial schema, first-load
sizing and large-page response-limit issues were corrected before completion.
The full offline suite was not required or run. Evidence and preimages are in
`.local/forward-pe-standardization/`. Only the manually launched Inspector was
reloaded; no recurring unit, provider operation or canonical data was changed.

## Reviewed reporting periods — September 23, 2026

The user authorized a manual most-likely review and production correction for
207 symbols from the September 22 incomplete calculation cohort. Matching now
uses reviewed_and_corroborated_fiscal_periods.v3. The fixed internal catalog
config/forward_pe_reviewed_periods.json contains 208 event decisions for those
207 symbols: the original cohort and Abivax's newer September 21 H1 release.

A decision requires the exact instrument, ticker, event natural identity and
source event date. Review time must be at or before the calculation cutoff.
An event correction records the original observation version, source date,
review evidence, confidence and content hash in the derived window; it does
not edit the canonical announcement or actual EPS/revenue. Corrected dates
remain date-only and become effective next session. A later, different event
cannot inherit an old reviewed decision. Annual and half-year reporting ends
may delimit the following estimated quarters; their reported EPS is never
substituted for quarterly estimated EPS. Most-likely associations are visibly
flagged inferred_reporting_period, including CRML's uncorroborated September
calendar event (inferred FY2026 end June 30).

The ratio still requires four consecutive usable quarterly estimates and all
existing price/basis guards. VFS's likely intended Q2 boundary is recorded but
financial_results_not_released blocks use as a reported result. FDXF's old
May-ending year and new December-ending year conflict with its retained
August/November/February/May forecasts; fiscal_calendar_transition preserves
the gap. HUBG uses a flagged preliminary H1 boundary without inventing actual
quarter EPS. The research ratio remains explicitly non-point-in-time and keeps
its pre-existing currency/share-basis limitations.

The bounded manual repair targets only the latest completed saved session and
an explicit cohort of at most 207 symbols. It preserves existing prices and
all older daily rows, may fill exactly one missing latest-session observation,
retains the previous publication, and uses the established export lock,
integrity checks and atomic pointer. The reviewed_period_correction lineage
records the new calculation and estimate cutoff. Replaying the same review
hash does not replace a published denominator with newer consensus estimates.
Future normal clock calculations load the catalog from the fixed internal
path; no recurring unit, provider workload or canonical-store writer changes.

Research, frozen source inputs, focused test logs and the publication and
post-publication verification receipts are retained under
.local/announcement-quarter-production-20260923/.
