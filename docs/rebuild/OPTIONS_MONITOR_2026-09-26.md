# Options Monitor

Decision: September 26, 2026. The user requested a private Sites dashboard and a collector on the existing WSL host, ThetaData Standard, transient raw responses, persistent aggregates, and 5/10/15/30-minute priority tiers. Notifications are the dashboard inbox plus opt-in browser Web Push. This new current-day monitor does not repeat or amend the completed historical ETF populations.

## Scope and ownership

The frozen roster in config/options_monitor_universe.json contains 2,251 equities and 15 ETFs (2,266 symbols). It was obtained through market.get_available_ticker@1.0.0, api_version 1.0; the exact response and SHA-256 are retained in the roster metadata. It is a current retained ticker universe, not a point-in-time optionability guarantee. Symbols without usable options remain missing.

- Site: sites/options-monitor, project appgprj_6ab7f05ab2788191bbfd221cc33736e5; owner-private audience.
- Collector: quant_data/options/monitor and quant_data/operations/options_monitor.py.
- Policy: config/options_monitor.json, version quant_data.options_monitor.v1.
- Local authority for saved monitor observations: data/.operations/options-monitor/monitor.sqlite, a derived research materialization. The Site D1 database mirrors aggregates and owns watchlists, alert reviews, and browser subscriptions.
- No recurring reads or writes of data/market.sqlite or data/options.sqlite. The existing canonical registry, migrations, populations, credential resolver, and historical collectors retain their separate ownership.
- Raw OHLC, OI, and Greek responses are decoded in memory only. Stored data consists of compact observations, selected leaders, quality flags, baselines, sanitized request receipts, and delivery state. Raw source replay is unavailable.
- WSL sends outbound HTTPS to the fixed Site origin. No inbound WSL service or tunnel. Site machine access uses the existing Sites dispatch token plus a separate collector secret. Connection material lives only in a mode-0600 local connection.json and Sites secrets; browsers receive no provider credentials.

## Clock and bounds

The new user timer ticks every five minutes from 09:00 through 16:55 America/New_York, weekdays. It is nonpersistent: missed host time does not trigger historical catch-up. Current calendar data gates regular and early-close sessions; out-of-session cycles publish a closed status. SPY, QQQ, IWM, DIA allow the additional eligible 15-minute option close.

Broad ETFs target 5 minutes; sector ETFs 10; prior-session top-100 liquid names 15; remaining equities 30. Saved watchlist members may select 5, 10, 15, or 30 minutes. An anomaly promotes a ticker to 5 minutes for 60 minutes. Priority is bounded by capacity and coverage is visible; timing is a target, not a throughput guarantee.

Each normal unit is limited to 2,500 provider requests, 256 MiB of received response data, and 240 seconds, with at most three concurrent requests and no implicit provider retry. Per market session limits are 60,000 requests and 8 GiB. The unit has a 270-second supervisor timeout and Restart=no. Current-day OHLC is full-chain, OI is once daily, and richer IV data is limited to market/sector/watch/flagged members. There is no historical provider backfill. Durable request admission prevents same-slot restart replay.

The dashboard refreshes saved data every five minutes. Manual reload reads saved aggregates; it does not call ThetaData. WSL must be running and network-connected during collection.

## Metric semantics

Volume and put/call activity are descriptive, not inferred trade direction. Interval volume subtracts comparable cumulative snapshots; decreases in either aggregate call or put volume, missing responses, changing populations, and incompatible intervals suppress derived signals. Offset corrections within one side cannot be reconstructed from discarded individual contract observations. Relative volume compares the same intraday bucket with at least 20 prior comparable sessions; it is not scaled from end-of-day volume. Initial deployment therefore warms up during normal trading days.

Metrics include calls/puts, acceleration, relative volume, put/call ratio, prior-session volume/OI, 0DTE and near-term shares, top-five concentration, leading contracts, quote spread, and a provider-derived IV proxy. Constant maturities of 7/30/90 days interpolate total variance only where brackets exist; missing terms remain null. First-order Theta IV is provider/trade-derived, not a guaranteed synchronized midpoint mark. OI effective date is the exact previous trading session where available, never an intraday positioning inference.

Alerts require relative volume >=3, interval volume >=500, and supporting acceleration >=3, IV change >=2 volatility points, or concentration >=0.6, plus acceptable data quality. Episode cooldown is 30 minutes. Watchlist alerts can be disabled. Local retention is 90 observed sessions; the cloud mirror retains 130 calendar days. These are compact monitoring records rather than raw contracts.

## Persistence and security

Local writes use the established physical-store lock, DELETE journaling, and immutable read procedure. Network calls precede write transactions. The Site accepts bounded, validated schema-version-1 publications; observation identities are symbol plus UTC capture time. Exact replay makes no observation change. Conflicting identities fail atomically. Older deliveries cannot replace newer latest observations.

Browser mutations require authenticated private-site access and matching origin. Watchlists are capped at 100 known symbols and browser subscriptions at 10. Push endpoints are restricted to known HTTPS browser push services; redirects are disabled in the sender. VAPID private material stays on WSL. Push delivery is one durable attempt per alert/subscription; the inbox remains the durable notification record. Browser permission must be granted using the Site's Enable notifications button.

## Validation and activation record

Primary checks: TypeScript compilation; 42 local API acceptance assertions covering authentication, cross-origin rejection, replay/conflicts, payload shape and limits, watchlist persistence, alert reviews, history, and unsafe push endpoints. Chromium exercised add/save/remove watchlist, alert toggle, ticker chart, notification readiness, and mobile layout with zero JavaScript exceptions. WebMCP is feature-detected; this installed Chromium does not expose document.modelContext, so its tool execution is unverified.

The collector's focused offline checks, fresh independent review, private deployment result, and unit activation receipt are recorded below when complete. No successful live Theta snapshot is claimed by these local fixtures. A weekend connection check must make zero Theta requests.

## Local operation

Runtime: .local/options-monitor-runtime/venv312/bin/python, Python 3.12, dependencies pinned in requirements/options-monitor.txt. Unit definitions live in deploy/systemd/quant-data-options-monitor.{service,timer}. Normal invocation supplies the explicit project root. Do not manually rerun a live collection cycle without a finite scope; use the timer's ordinary clock execution.

Site development uses its bundled npm scripts and local D1 state. The API integration script uses a clearly named local test collector token, localhost:5183, and synthetic local D1 observations only. It is not a production seeding command. Site production contains no fixture observations.

## Completion evidence — September 26, 2026

Private deployment succeeded at 17:09:53 UTC:
https://quant-options-monitor.yanggainan.chatgpt.site
Source commit: 5d1f52912d8cb1c3db5d141085f0d40a4fc3abe5.
Deployment: appgdep_6ab7fc5822cc819198342eaeb7fc8898.
The Site's initial D1 migration applied successfully through deployment.

Fresh independent review resolved six initial findings and passed the focused correction gate: 15/15 isolated Python tests, plus independent temporary-SQLite execution of exact Site SQL for concurrent subscription limits and retention. Primary obtained 42 local API assertions, successful compilation/build, browser workflow/mobile checks, and an additional missed-Monday-heartbeat regression. Web Push encryption/signing/timeout/redirect handling passed with ephemeral keys and a mocked HTTP response; private VAPID pairing and connection file permissions also passed.

At 17:10:15 UTC the guarded initial connection check published the frozen roster and closed-market heartbeat with Theta requests forbidden. Production D1 inspection found the expected seven tables, exactly 2,266 universe rows, zero observations, and the closed-market state. No fixtures were deployed.

The new user timer was enabled and active/waiting. A single actual unit verification finished at 13:11:19 EDT with Result=success, ExecMainStatus=0, market_closed and requests=0. Next timer wake-up: Monday September 28, 2026 at 09:00 EDT; the calendar gates market data at the open. The derived store had zero attempts, observations, and alerts at handoff. Existing recurring units were not changed.

Private receipts: .local/options-monitor-build/{initial-connection,installed-units,activation}.json. The initial connection and unit verification were two distinct zero-Theta weekend publications. Live provider response compatibility, full-universe throughput, and actual browser delivery remain to be observed during ordinary operation. Relative-volume baselines need at least 20 comparable prior sessions. Enable browser notifications from the Site on each desired device.

## Storage and personal watchlists — September 26 follow-up

The user chose 25 tickers per user, 100 unique watchlist tickers across the Site, and rolling 90-session intraday history. Per-user rows use the platform's stable authenticated identity; the collector receives a deduplicated symbol union with minimum cadence. Personal push opt-outs do not mute another subscriber. Alert review state remains shared.

Local observations retain 90 sessions; request attempts/receipts now retain seven sessions; acknowledged outbox copies are deleted immediately. Cloud observations/latest/alerts retain at most 90 observed sessions and the existing 130-calendar-day age ceiling. A small cloud session index makes retention bounded without scanning every saved payload. The old shared watchlist was verified empty before adding the new table.

See [the storage audit and estimate](OPTIONS_MONITOR_STORAGE_2026-09-26.md) for actual checks and sizing. Daily ETF history remains authoritative in options.sqlite; the historical chart adapter is not implemented by this limits/retention change. No new provider or scheduler workload is introduced.

The personal-watchlist/storage update was privately deployed successfully at
2026-09-26T23:43:29Z from Site source commit
0e8939bad6d0db498652a5e7106c69232097611d. Deployment
appgdep_6ab85899e5288191b35241acf1d1b363 retained the existing audience. Both
additive migrations applied. Final TypeScript/build checks passed; 18 collector
tests, three exact-SQL concurrency/isolation tests, and 98 local API assertions
passed. Chromium workflow checks had no JavaScript exceptions. No provider call
or recurring-unit restart was performed. Deployment evidence and the storage
benchmark are in .local/options-monitor-storage-review/.

## Daily historical charts — September 26 follow-up

The user explicitly requested the historical chart connection after accepting the
storage/watchlist changes. `options.sqlite` remains authoritative. The private
Site receives a reproducible, compact `daily_history` serving cache, with no
second daily archive in `monitor.sqlite` and no historical provider request.

The registered read-only consumer uses `OptionsStore.read()` under the existing
physical store lock. Network work begins after releasing that lock. It projects
current daily captures into ETF/session totals, call/put ratio, prior-session OI
when complete, paired selected-anchor ATM IV at 7/30/90-day constant maturities,
and source capture/hash/version metadata. Missing totals stay null. IV uses
paired call/put anchors, same-session underlying references, and total-variance
interpolation without extrapolation; it is a selected-contract EOD proxy, not
an identical mark to live trade-derived IV.

Cloud publication accepts at most 500 points and 900 KB per request. Cursor and
points commit atomically; stale cursors and conflicting identities fail. A newer
source capture replaces its ETF/session projection. Lost acknowledgments resume
from the committed cloud cursor without replaying the completed source page.
The existing out-of-session monitor cycle may sync one page; its timer and
provider limits remain unchanged. With the present weekday timer, a source
update after the evening cutoff is normally visible after the next weekday
09:00 cycle. This does not create or enable an EOD data collector.

The finite initial sync is bounded to capture ID 37863 (37,863 current records
across 15 ETFs, source inventory through September 23, 2026), at most 80 Site
POSTs, no hidden retry, 30 minutes, and zero ThetaData calls. Credentials and
private access controls are unchanged. Runtime completion is recorded separately.

The ticker panel has Intraday and Daily history tabs. Daily ranges are 1M, 3M,
1Y, 5Y, and All (up to 3,000 records), with aligned IV, stacked call/put volume,
and put/call charts. Seven- and ninety-day IV can be enabled. Missing historical
sessions use the existing SPY daily-session grid and remain gaps. Latest saved
session and IV coverage are explicit. Single-name daily history remains
unavailable; its intraday history is selectable by recorded session.

Impact map: read-only options source projection, private atomic daily cache,
closed-cycle integration, and the ticker charts. Checks are the monitor history
model/reader/collector tests, daily local API integration, TypeScript/build,
and desktop/mobile browser workflows. Existing canonical migrations, shared
publishers, and other dataset consumers are unchanged.


Daily-history completion: private deployment succeeded at
2026-09-27T00:20:33.767945+00:00 (September 26 evening Eastern), from Site commit
921d8a981bb1d0f14d7cfc9a8e04659be69182ac. Deployment
appgdep_6ab86149c55c8191b72f268996d9ece4 applied the additive daily_history table
and retained owner-private access. Native database inspection confirmed the new
table and real projected rows.

The finite initial export completed with status up_to_date: 37,863 points in
76 successful POST batches, ending at capture ID37863, zero ThetaData requests.
Native cloud readback confirmed that checkpoint at 2026-09-27T00:24:52.270Z.
Coverage is 15 ETFs, latest source session September23,2026; older coverage varies
by ETF. Canonical before/after checks matched inode, size, modification time,
capture/current counts, and high-water ID; no WAL was present. The established
immutable reader and separate temp-store byte-preservation tests were used;
this operational check did not hash the entire 8.67GB source file.

Validation passed: 35 focused Python tests, 26 local HTTP assertions, final
TypeScript and production build, local desktop/mobile chart workflows with zero
JavaScript exceptions, and fresh independent reader/SQL/migration review. The
review's small invalid-range finding was corrected and rechecked. WebMCP runtime
execution remains unverified because Chromium did not expose modelContext.
Actual live intraday provider compatibility and browser push delivery remain the
prior ordinary-operation observations, not evidence obtained from this export.

Receipts are in .local/options-monitor-history/: initial-sync.jsonl,
cloud-final-checkpoint.json, canonical-{before,after}.json,
final-build-and-deployment.json, independent-review.md, browser-review.json,
and cache-size-estimate.json. No recurring unit was run, restarted or rescheduled;
no source database or collector credential was changed by this follow-up.


## Chart visibility and freshness correction — September 26 evening

After the user reported missing charts and an apparently stale timestamp, the
main market page now opens an inline ETF history chart card (SPY default,
15-ETF selector). Desktop shows aligned IV, call/put volume, and put/call plots;
mobile stacks them. Ticker detail charts remain available. The main page now
separates historical session date, history-sync time, intraday observation time,
page refresh time, and the collector check-in. The intraday table is explicitly
labelled and weekend closure uses a neutral status.

Historical refresh previously depended on collector.last_seen, so an open chart
could retain an empty/old history result when only the daily cache changed. It
now refreshes after each successful dashboard reload and when the tab becomes
visible. No provider, store schema, collector schedule, or source publication
semantics changed.

Checks passed: 30 local API assertions, TypeScript, production build, actual SVG
plot/axis checks and desktop/mobile visual review, ETF selection/detail behavior,
and a regression that publishes a local historical revision while leaving the
collector heartbeat unchanged. Early full-page screenshots missed the chart
paint; native viewport screenshots after foreground/animation frames confirmed
visible plots. Private user-browser automation was unavailable (sandbox helper
failure), and dispatch-only diagnostic requests to browser-authenticated APIs
correctly returned401. No authentication bypass was added. This turn did not
verify a production user-browser session or make any provider request.

Deployment appgdep_6ab872947df8819186184bd79ffc020d succeeded at
2026-09-27T01:34:20.382613+00:00 with source
713c717212041ff70ee1f923d7a755a2a71b4a27. Existing owner-private access was
preserved. Fresh immutable source coverage is recorded in
.local/options-monitor-freshness/completion.json. Existing timer inspection
showed active, next weekday09:00ET wake-up on September28; no timer or unit was
manually run or changed. Missing September24–25 history is not manufactured or
silently fetched by this UI correction. A previously open browser page needs one
full page reload to receive the new layout.


## Simplified update times and EOD clarification — September 26 evening

The prior multi-clock strip was confusing. The market page now leads with
ETF daily data through; watchlist/radar lead with Intraday data as of or an
explicit no-observations state. Watchlist freshness excludes other tickers.
Collector contact, daily-cache copy time (market only), and browser check time
are available under collapsed Update details. Check for updates reloads saved
results; it does not substitute the browser clock for the data timestamp.
The table cadence is labelled Scan interval.

Only app/monitor.tsx and app/monitor-extra.css changed in the Site.
TypeScript, production build, final diff check, and local Chromium checks passed:
one main timestamp, expanded details, unchanged data time after page reload,
empty state, unrelated-newer-ticker exclusion, rendered ETF chart paths, and
desktop/mobile visual review. Fixtures stayed local. The build retained its
existing advisory about large client chunks. No production user-browser session
was automated; production completion uses the native succeeded deployment receipt.

Private deployment appgdep_6ab87e165e708191be30d1d57e80b735 succeeded at
2026-09-27T02:23:26.091590+00:00 from Site source
4d0e9b91606995774593ea7cf911ce879a571f2b. Access is unchanged.
Receipts: .local/options-monitor-time-labels/browser-review.json and completion.json.

Operational clarification from source and read-only timer inspection:
monitor.sqlite saves compact observations and alerts throughout the session.
There is no dedicated monitor end-of-day daily rollup/finalization, and no
automatic ThetaData daily incremental job feeding options.sqlite is configured.
The existing daily-history data came from a finite backfill. The normal monitor
cycle copies existing source projections; it cannot create a new source session.
The separate older Alpaca ETF-surface close job (timer retains the historical
quant-data-alpaca-spy-options name, service runs alpaca_etf_options_refresh) does
not feed the current ThetaData chart source. Its existence is not an EOD updater
for this monitor. No recurring unit was changed or manually run by this fix.

Watchlist and radar baselines require 20 valid prior matching symbol/cadence/
time-bucket sessions. Shared matching history is reusable; switching from
30-minute radar observations to a 5-minute watchlist requires a matching new
baseline. Intraday persistence is enough for warm-up without a daily rollup.
WSL performs collection, aggregation, anomaly evaluation, and push delivery.
Scheduled authenticated HTTPS publications copy compact results to the private
Site cache; browsers read that cache every five minutes. WSL downtime leaves
saved results viewable but prevents new collection and alerts.


## Later daily-source collection decision — September 26

The user's subsequent request adds daily Theta EOD collection and weekend
missing-session repair for the same 15 ETFs, documented in
[the daily collection contract](THETA_DAILY_COLLECTION_2026-09-26.md).
This supersedes the earlier statement that no recurring Theta EOD updater is
configured once activation is recorded there. The historical chart reader,
intraday monitor, watchlist scope and Site publication policy are unchanged.
New canonical capture IDs remain eligible for the existing history-cache sync.


## Market-open startup repair — September 28

Read-only investigation at approximately 11:09–11:15 ET found the normal 09:30
run failed importing ThetaClient because python-dotenv was absent from the
monitor virtual environment. Theta SDK 1.0.11 imports dotenv but omits it from
Requires-Dist. The calendar reservation had committed before SDK import; no
calendar receipt or intraday observation existed. Later scheduled cycles returned
calendar_attempted without publishing a heartbeat. Private Site inspection found
zero latest rows and collector.last_seen 2026-09-28T13:25:00.619Z (09:25 ET).

The user explicitly approved applying the prepared repair and one September 28
calendar recovery request, with no retry and a two-minute ceiling. The repair
pins python-dotenv 1.2.3, imports SDK dependencies before durable provider
admission, and publishes a sanitized failed heartbeat on startup/blocked-calendar
paths. Provider reservation/replay guards, source databases, timer definitions,
roster, cadences and existing provider caps are unchanged. The four reviewed
files were applied byte-for-byte from the isolated tested candidate.

The dependency was installed with the existing uv tool from its local cache into
.local/options-monitor-runtime/venv312; no package download was needed. The real
SDK import preflight passed without client construction or network. Twenty-three
monitor tests and seven history tests passed in temporary roots. The initial
history test import lacked a copied helper fixture; supplying it resolved the
harness error and all seven checks passed. Three changed startup cases were
rechecked after the final failure-message review. Two temporary-store recovery
checks proved one-attempt enforcement, malformed-calendar rejection and
preservation of the original failed record.

The approved manual operation used the established THETA_DATA_API resolver,
MonitorThetaTransport, physical monitor-store lock and calendar publisher. It
was limited to one authentication/client initialization, one calendar_on_date
request for September 28, 1 MiB received, a 90-second request budget and 120-second
supervisor. It completed with requests=1, received_bytes=59, and zero manual
option-snapshot requests. The original 09:30 admission remains preserved;
a separate explicit-recovery-2026-09-28 attempt has a successful receipt. The
calendar now records open 09:30 / close 16:00. No recurring unit was manually
started or retried. The 11:20 normal trigger encountered the recovery run.lock
and skipped; the next normal trigger remains 11:25 ET. Actual subsequent data
publication is recorded separately below after observation.

Artifacts: .local/options-monitor-repair-20260928/{repair.patch,
original-hashes.json,readiness.json,recovery-execution.json,post-recovery-store.json}.


The ordinary 11:25 ET cycle completed at 11:25:43 with partial status:
1,485 data requests, 240 observation publications in three batches, 1,768,485
received bytes, 1,001 per-symbol ProviderFailure outcomes, and 1,025 capacity-
deferred candidates. Private Site readback confirmed last_seen 15:25:42.169Z
and real latest rows. This proves the original startup block and upload path are
repaired; it does NOT establish healthy market coverage. Of those 240 records,
only one had usable volume (one baseline_building, 197 incomplete, 42 no_data).
The ordinary request ledger recorded 1,000 OHLC UNAUTHENTICATED outcomes and one
OI UNAVAILABLE outcome. The aggregate flag future_row covers missing, older-
session, and future timestamps, so these saved flags alone do not establish the
specific timestamp cause. DIA had a usable IV proxy but null volume.

No second manual provider request or automatic replay was attempted. Further
repair requires inspecting current provider timestamp shape and investigating
SDK session sharing; authentication invalidation between concurrently created
clients is only a hypothesis at this point. A proposed separate diagnostic is
one SPY option_snapshot_ohlc for September 28, at most 16 MiB and two minutes,
no retry and no raw-contract retention. This was not included in the consumed
calendar-recovery authorization and remains unexecuted pending approval.
See .local/options-monitor-repair-20260928/completion-partial.json.


## Snapshot integration repair — September 28, 12:11 ET

The user explicitly approved the separately proposed one-SPY snapshot diagnostic
and correction of the remaining integration issues. An initial nonblocking
run.lock check found the normal 12:05 cycle active and exited before reserving
or authenticating; this consumed no provider request. At 12:06:42 ET, the one
approved option_snapshot_ohlc request completed with 168,087 received bytes and
7,791 rows, within the one-request/16-MiB/two-minute/no-retry bounds. Only field
counts, timestamp classifications, aggregate volumes and a sanitized receipt
were saved; individual contract rows were not retained.

The provider returned 4,637 current-session rows (volume 6,013,360) and 3,154
prior-session rows (old volume 314,754), spanning September 22–28. There were no
future timestamps in this sample. The old future_row flag had conflated prior
sessions with genuinely invalid/future times. The current snapshot's last OHLC
records can therefore belong to earlier trading sessions. The official endpoint
is described as the latest OHLC for a contract's trading day:
https://thetadata.net/docs/operations/option_snapshot_ohlc.html
The actual mixed-session behavior above is live diagnostic evidence; it is not
asserted by the generic documentation alone.

The monitor now excludes prior-session OHLC volume/count from today's totals.
It retains those returned contract identities with zero current-session
contribution in memory, so first trading today does not falsely change the
population. This is a monitor-only current-session interpretation of latest
OHLC, explicitly flagged prior_session_ohlc_excluded. It does not alter provider
records or their timestamps. At least one valid current-session row is required:
an all-old response remains incomplete/missing, not a certified zero-activity
snapshot. Missing/naive/future timestamps, missing current volume, conflicting
duplicates, removed contracts and negative corrections still suppress unsafe
intervals and alerts. Old saved aggregates are not rewritten or backfilled.

The parallel transport now creates one authentication session per normal cycle
and reuses it through the installed SDK's existing_authorized_client option.
Each worker retains its own channel, stream accounting and receipts. Factory
initialization is serialized; failed initial authentication is not repeated by
other workers. The session is process/cycle-local. Existing credential resolver,
three-worker cap, request/byte/time budgets and replay admissions stay unchanged.

Impact: private monitor snapshot aggregation and authentication only; consumers
are monitor observations, baselines and existing Site display. No canonical
facts, shared time/identity contracts, schema, scheduler or UI deployment changed.
Validation: 36 distinct focused monitor/history/transport tests passed, including
mixed sessions, first-trade transitions, all-old/invalid timestamps, duplicate
conflicts, population removal, one per-cycle factory, shared login isolation and
no repeated failed authentication. Source hashes were checked and the reviewed
patch applied under run.lock at 2026-09-28T16:11:34.174449+00:00. No recurring
unit was manually run. The ordinary 12:15 ET cycle is the next live verification.

Artifacts in .local/options-monitor-repair-20260928/: snapshot-diagnostic.json,
integration-original-hashes.json, integration.patch, integration-staged/ and
integration-applied.json. The transient 12:05 private-Site SSL failure remains
visible in the journal; existing durable outbox delivery on later scheduled
cycles remains unchanged, with no new ad-hoc retry added.


Snapshot integration completion: the normal 12:15 ET run completed at 12:15:07
with status complete, eight data requests, 546,328 bytes, four observations,
one successful Site batch, zero per-symbol errors and zero pending outbox rows.
All four broad ETFs (SPY, QQQ, IWM, DIA) have non-null call/put volume, put/call
ratio and 30-day IV. Request receipts show four successful OHLC and four successful
first-order Greeks requests, with no authentication errors in this cycle. Private
Site state readback confirms status complete and last_seen
2026-09-28T16:15:05.932Z. Example SPY totals at capture: calls 2,624,146,
puts 3,589,537, P/C approximately 1.36789 and 30-day IV approximately 13.4988%.

The first corrected observations retain a contract_universe_changed flag versus
earlier bad/missing populations; current levels remain valid while unsafe
cross-change intervals stay missing. No prior aggregates were rewritten. Relative
volume/alerts still need 20 comparable prior sessions. Sector and radar updates
follow their existing cadences; this four-ETF check does not certify full-universe
throughput. The Site UI/source was not changed or redeployed, and no production
user-browser session was automated. Evidence: integration-completion.json and
first-corrected-observations.json in the repair artifact directory. No additional
manual provider request, retry, service start or scheduler change was performed.


## Staggered universe collection — September 28, 12:40 ET

The user explicitly approved dividing ordinary 30-minute radar collection into
six groups, with one group's normal eligibility beginning every five minutes.
The collector now assigns a stable SHA-256-derived symbol offset of 0, 5, 10,
15, 20 or 25 minutes from the regular-session open. Assignment is independent of
roster order and watchlist changes. The frozen 2,251-equity roster distributes as
362 / 390 / 351 / 393 / 374 / 381 before priority exclusions. A ticker retains
its 30-minute cadence. Broad/sector ETFs, every saved watchlist member (including
30-minute watchlists), top-100 liquidity promotions and active 5-minute alert
promotions keep their existing eligibility rules and priority ordering.

Request admissions retain the original aligned half-hour identity. Observations
and baseline lookups use the shifted scheduled time, while captured_at remains
the actual observation time. This preserves old success/failure reservations
across a midday rollout without replaying a request at its new phase. Additional
checks prevent replay behind a newer aligned attempt or a phase already attempted
under a faster priority tier. Existing aggregates and receipts are not rewritten.
A changed observation time warms its matching baseline; the existing cadence and
interval-gap gates suppress incompatible transition intervals.

Unstarted work stays eligible on subsequent ticks until the next shifted slot;
a deferred scan can cross a wall-clock half-hour boundary. A mid-session cold
start can therefore expose several eligible groups as backlog. It remains
bounded by the same three workers, cycle/session request and byte caps, deadline,
and zero-retry policy. There is no historical provider catch-up. Normal group
eligibility respects the regular and early market close, without forcing all
groups into a simultaneous closing scan.

Impact: monitor-only candidate eligibility, private admission/observation slot
mapping and time-matched baseline selection. No shared time contract, migration,
store schema, roster, watchlist limit, provider endpoint or Site UI change.
Validation: 46 focused monitor/staggering/transport/history tests passed in an
isolated source copy, using temporary databases and fake providers/Site clients.
Ten stagger-specific checks cover deterministic phases, two full rotations,
restart and legacy-failure replay, current-window and priority-transition guards,
capacity carryover, priority overrides, baseline selection and early close.
Initial test setup issues (cold-start backlog expectations, fixture-induced
liquidity promotion and absent static registry fixtures) were corrected and the
complete suite rerun successfully; production logic was not changed for those
fixture issues. Final review added the priority-demotion replay guard and its
regression before the final 46-test pass.

The tested source was applied byte-for-byte under the idle collector run.lock at
2026-09-28T12:40:54.710239-04:00. Existing uncommitted monitor prerequisites and
unrelated changes were preserved. No manual provider request, timer edit, service
restart, Site deployment, commit or push occurred. The ordinary 12:45 ET run will
load the new source; already-attempted current-window names remain suppressed,
so the first full new normal rotation is 13:00–13:25 ET, subject to capacity.
That full live rotation has not been observed in this implementation handoff.

Pre-application journal evidence: the 12:30 cycle used its 2,500-request cap,
published 1,576 observations, deferred 688 candidates and recorded two provider
failures. A 12:35 Site SSLV3_ALERT_BAD_RECORD_MAC upload failure was followed by a
successful ordinary 12:40 cycle (17 observations, 34 requests, five publication
batches, zero per-symbol errors and zero capacity deferrals). These are prior
operational observations, not evidence for the new staggered rotation. Upload
retry/outbox policy was not changed.

Evidence: .local/options-monitor-stagger-20260928/{stagger.patch,
original-hashes.json,validated-hashes.json,initial-test-result.json,
tests-final.log,applied.json}. The active monitor source changes are policy.py
and job.py; tests are test_monitor.py and the new test_monitor_stagger.py.


## Full-chain interval volume — September 28, 15:50 ET

The user explicitly approved implementing full-chain cumulative volume deltas
and wiring them to the Site UI. This supersedes the identical-contract-fingerprint
requirement for interval volume described above. Open-interest compatibility
still requires matching contract coverage; its rules are unchanged.

The collector subtracts previous same-session call and put totals separately.
Newly appearing/active contracts contribute their reported volume through that
difference. A changed contract fingerprint is informational and no longer alone
suppresses a valid interval or its anomaly comparison. First observations,
incomplete snapshots, invalid/cross-session timestamps, decreases in either
side's cumulative volume, and detected decreases in total or active contract
counts still produce missing interval volume. The transition from old records
without coverage counts can produce one additional gap when fingerprints differ.

Only compact optional fields were added to observation JSON: interval_call_volume,
interval_put_volume, interval_start, contract_count and active_contract_count.
There is no schema migration or raw-contract storage. Existing records are not
rewritten or backfilled. Successful full-chain responses remain the provider
completeness premise: compact counts detect net coverage loss but cannot prove
that simultaneous additions and omissions did not occur. No new live contract
membership diagnostic was executed or claimed.

Positive elapsed intervals retain their actual start/end/duration even after a
collection delay or cadence change. Such intervals are descriptive chart data;
interval_gap/cadence_changed observations are excluded both from current alerts
and from future normal-cadence baseline samples. Correction/incomplete/coverage
failures remain gaps. Existing 20-session warm-up, trigger thresholds, priority
phases, request admission identities and request/byte/worker/duration caps remain.

The Site API accepts both old and new observation payloads. Charts consume the
explicit call/put interval totals, sort same-symbol session observations, show
actual elapsed minutes in volume tooltips and detail metrics, and explain gaps.
Older already-valid intervals retain their existing cumulative-difference display;
older rejected intervals are never reconstructed in the browser. Daily charts
and cumulative-volume display retain their original behavior.

Validation impact: monitor aggregation/derivation, baseline eligibility, collector
publication compatibility, and intraday volume charts. The isolated Python suite
passed 57 tests (monitor, interval, staggering, transport and historical-cache
consumers). The Site passed 13 chart-adapter checks, 10 local-D1 API/replay/history
checks, TypeScript and the production build. Desktop and 390-pixel mobile browser
checks covered new-contract bars, valid zero intervals, ten-minute gap tooltips,
correction gaps, cumulative/daily toggles and layout. Initial browser setup clicked
before hydration; its readiness wait was fixed. Visual review caught and fixed
tooltip text overflow, and the browser assertions passed again. Build emitted
only its existing large-client-chunk advisory. No full-suite trigger applies.

The shared Site was published first as version 6, source
0d0bda5d98ec9ed696a623bf61fb55f1ab456706, deployment
appgdep_6abac4f3292c8191bf74a7d101676579 (succeeded). Existing custom sharing was
preserved via the general deployment operation. Tested Python bytes were then
applied under the idle run.lock at 2026-09-28T15:50:56.036521-04:00. No manual
provider call, service start/restart, timer change, canonical write or parent
repository commit/push occurred. The next ordinary 15:55 ET cycle loads the
change; valid new interval bars can require two comparable new observations.
Production observation/upload readback after this application is not yet obtained.

Evidence: .local/options-monitor-interval-20260928/{interval.patch,
original-hashes.json,validated-hashes.json,tests-first.log,applied.json,
browser-review.json,validation.json,options-monitor.tar.gz}. Collector files:
model.py, job.py and store.py in quant_data/options/monitor. Site files:
app/interval-volume.ts, app/history-panel.tsx, app/monitor.tsx,
app/monitor-types.ts and app/api-lib.ts, with focused regression tests.


## Five dashboard priorities — September 29, 2026

The user approved all five improvements, then explicitly declined restarting
Ubuntu WSL during an interruption. On September 29 the user reported recovery
and requested completion. Work resumed without any WSL or collector restart.
This implementation is Site-only and reads the already-published aggregates.

1. Coverage counts are scoped to the selected ETF, watchlist or universe view.
   Freshness and baseline readiness are separate: unavailable/overdue snapshots
   are explicit, closed-market observations say latest session, and anomaly
   evaluation requires a valid interval with at least 20 comparable sessions.
2. What changed ranks usable unusual interval volume, positive 30D IV changes
   from the session anchor and interval put/call shifts from the preceding
   comparable observation. Watchlist columns now include these interval metrics.
   Radar filters separate confirmed alerts, developing activity, warming-up and
   overdue names. Rankings are descriptive and do not send notifications.
3. Fifteen market/sector ETF tiles support relative volume, 30D IV change and
   interval put/call. Selecting a tile opens that ETF's intraday chart, including
   switching back from daily history. Gray indicates missing/overdue metrics.
4. Interval charts can overlay the prior-session median and middle 50% range.
   Authenticated /api/volume-profile reads at most 9,001 compact projected rows,
   uses the latest 9,000, and reports truncation. It uses existing indexed
   (symbol,captured_at) access, a 130-calendar-day ceiling, one sample per session
   and Eastern five-minute capture window, matching cadence, and at most 60
   prior sessions. At least 20 are required. Selected/future sessions, invalid,
   incomplete, corrected, gap and cadence-transition intervals are excluded.
   Zero volumes remain real zeros. No comparison records or raw data are saved.
   Capture-window chart comparisons can differ from the collector's scheduled-slot
   alert baseline; that distinction is explained in the UI. Missing comparison
   data never removes observed bars; warm-up, failure and retry are explicit.
5. Alert evidence joins the exact symbol and detection-time observation. Observed
   interval, recovered expected median, RVOL, baseline count, supporting signals
   and quality context describe detection time. Missing old evidence is explained
   without substituting current values. A link requests the original session's
   chart and marks the alert observation. Latest snapshot metrics below the chart
   are labeled separately. Existing alert review and browser push behavior remain.

Implementation files are in sites/options-monitor/app: monitor-insights.ts,
coverage-summary.tsx, activity-highlights.tsx, etf-heatmap.tsx, volume-profile.ts,
insight-queries.ts, alert-inbox.tsx, monitor.tsx, monitor-types.ts,
history-panel.tsx, market-history.tsx, monitor-extra.css and the dashboard,
history and volume-profile API routes. Tests add insights.mjs,
insights.integration.mjs, insight-queries.py and fixture/loader helpers.

Validation impact: Site-derived presentation, authenticated aggregate reads and
historical chart navigation. Passed 36 calculation checks, 15 local-D1 API checks,
9 exact-SQL/in-memory checks, and the existing 13 chart-adapter plus 10 API
regressions (83 total). TypeScript, final diff whitespace and production build
passed. Browser QA passed 13 grouped scenarios with local synthetic overrides,
including desktop/390-pixel mobile, every heatmap metric, radar filters, warm-up,
expected ranges, retry, cumulative/daily regression, original alert values and
session marker, and empty states. No browser runtime exceptions were recorded.
Visual review corrected an empty warm-up comparison layer and tightened layout.
The build retained its pre-existing large-client-chunk advisory. No cross-domain
full-suite trigger applies; no production provider or notification-delivery check
was executed or claimed.

Sites version 7 deployed successfully at 2026-09-29T14:58:56.519034Z:
- Source: 7a7d33c49903efefcf0f4b5ed20393be61f29d94
- Version: appgprj_6ab7f05ab2788191bbfd221cc33736e5~appgver_c073d48a88948191b5a3d9161da31ba7
- Deployment: appgdep_6abbd228849c81918e769cee5e4e7490
- URL: https://quant-options-monitor.yanggainan.chatgpt.site/

Existing custom sharing (owner plus one external viewer) was preserved through
the general deployment operation. Only the nested Site source was committed and
pushed by the Sites workflow. No parent repository commit/push, schema migration,
provider request, timer/unit change, collector source edit, canonical write,
production fixture write or WSL restart occurred. Existing storage retention and
collection cadences remain unchanged. Completion evidence is under
.local/options-monitor-five-priorities/ (browser-review.json, validation.json,
completion.json and options-monitor.tar.gz); the earlier interruption record is
preserved in RESUME.md below its completion update.


## History visual refinement — September 29, 2026

The user requested a visual improvement to the ETF history section. Site files
app/history-panel.tsx and app/monitor-extra.css now present three aligned chart
cards with headline values and timestamps from the selected session, compact
history/session controls, consistent plot heights and lighter grids. Expected
volume status sits below the cards; unavailable interval counts remain visible
and methodology/gap explanations expand on demand. Daily history uses the same
cards, and windows shorter than 120 calendar days label individual dates rather
than repeating month/year ticks. Shared ticker detail uses the same presentation.

No calculation, API, schema, provider or collector changes were made. Missing
latest values remain missing; the cards do not replace them with earlier values.
Validation: 13 existing interval-adapter checks, TypeScript, production build,
whitespace review and 14 grouped browser scenarios passed. Visual review covered
1680/1440 desktop, 390 mobile and an 840-pixel narrow viewport. Browser fixtures
covered warm-up/mature/error/empty states, all chart toggles, daily date labels,
gap notes and original-session alert navigation; zero runtime exceptions. The
final browser harness corrected its selector for separately rendered axis ticks.
Existing large-client-chunk build advisory remains. No full-suite trigger applies.

Version 8, source c0126b1d28a1e18f4cadedf7ca800e7a22070fd6, deployed successfully
at 2026-09-29T15:54:16.040644Z as appgdep_6abbdf1ede648191a9fc1f5262fb1f7c.
Custom sharing was preserved. Only the nested Site source was committed/pushed;
no parent commit/push, provider call, canonical write, service/WSL restart or
schedule change occurred. Evidence: .local/options-monitor-history-visual/
(completion.json, browser-review.json, screenshots and options-monitor.tar.gz).


## Local agent readers — September 29, 2026

The existing saved monitor observations are now exposed through
`options.get_intraday_snapshot@1.0.0` and
`options.get_intraday_history@1.0.0` in the shared local launcher.
The [agent tool contract](OPTIONS_MONITOR_AGENT_TOOLS_2026-09-29.md) defines
bounded immutable reads, capture cutoffs, observation hashes, age, quality and
retention limits. This addition does not change collectors, saved observations,
watchlists, schedules, Site behavior or publication.

## September 30, 2026: historical intraday volume bootstrap

The user explicitly approved a one-time bootstrap of the last 30 completed
trading sessions across the configured roster. This supersedes the original
monitor-only warm-up restriction for this scoped backfill; canonical historical
ETF populations and recurring units retain their separate authorization.

`quant_data.options.monitor.backfill` provides explicit plan, run and status
commands. A plan freezes symbols, completed sessions from the existing cached
Theta calendar, request/byte caps and a UTC deadline. One worker processes ETFs,
watchlist names, then the rest of the universe. Normal bulk work waits outside
09:00–17:00 Eastern on weekdays. A separately capped pilot permits at most two
ticker-days and 50 requests during the session. There is no new timer, automatic
restart or provider retry. WSL must remain running; an interrupted run resumes
only against its unchanged plan and remaining allowance.

The provider methods are historical trade-contract discovery and full-strike,
both-rights five-minute OHLC for each discovered expiry, including expirations
that are now expired. Bar timestamps are starts of half-open intervals. Calendar
early closes bound each day. Empty catalog/expiry responses, malformed values,
duplicate bars, request failure or decoding limits leave the ticker-day missing.
Zero bars rely on the provider's successful, complete full-chain response
contract; silent upstream omissions cannot be independently ruled out. Raw
contract rows are discarded after each response.

The additive, checksummed private extension leaves the v1 monitor schema bytes
unchanged. `baseline_days` stores one compact JSON array of call/put volume per
ticker-day. `baseline_requests` holds durable admissions, sanitized receipts and
temporary aggregate expiry checkpoints; completed days discard checkpoint
payloads. `baseline_runs` freezes finite workload manifests. Failed or uncertain
attempts cannot be replayed automatically. Exact day replay changes nothing;
conflicting content is rejected. The established physical store lock protects
writes, and network calls happen outside write transactions.

The live engine combines historical seeds and recorded live intervals by date,
cadence and Eastern collection window; comparable live data wins per date.
Five-minute seeds support 5/10/15/30-minute windows, including staggered radar
phases. Both actual interval endpoints must be within one minute of the seed
window. At least 20 comparable prior sessions are still required. Current/future
sessions, bad coverage, corrections, cadence changes and collection gaps do not
enter comparisons. These seeds establish volume references, not IV history.

The Site mirrors compact days through authenticated `/api/collector/baselines`.
Both publication paths enforce combined 90-session retention with the existing
130-calendar-day ceiling; daily options.sqlite history is separate. Local
retention likewise counts seeded and live sessions together. Durable backfill
admissions remain as a compact no-repeat ledger. Charts reuse the expected
median and middle-50% band. Seeds never enter latest observations, intraday
charts as invented observations, heartbeat freshness, or historical alerts.
Alerts evaluate only subsequent normal live observations.

Operational scope for the first measured pilot: SPY and AAPL, September 29,
2026 only; at most 50 data requests, 512 MiB received bytes and 900 elapsed
seconds, one worker, zero retries. Existing THETA_DATA_API resolver and fixed
Site credentials are reused. The bulk target is 2,266 symbols x 30 sessions,
August 18–September 29, 2026 (67,980 ticker-days); its exact finite manifest and
activation evidence are recorded separately after the pilot. A cap does not
authorize more requests, retries, a later deadline or recurring execution.

Pilot result: three data requests were attempted. Contract discovery and the
first historical expiry succeeded (5,394 catalog rows; 15,563 OHLC rows); the
second expiry returned UNAUTHENTICATED. Total received bytes were 216,221.
The successful expiry was reduced to volume bins; no complete ticker-day was
saved or published. The coincident normal collector login is a possible cause,
not a confirmed diagnosis. No provider retry occurred.

The remaining finite batch has a ceiling of 599,997 new data requests,
68,719,260,515 received bytes and 14 days from plan creation; including the pilot,
the approved operating ceiling is 600,000 requests and 64 GiB. It uses one worker,
starts outside 09:00–17:00ET weekdays and pauses at every request boundary during
that window. A fresh client is obtained after such a pause, without retrying any
failed admission. Provider failures stop the process; there is no recurring
service, auto-restart, or automatic deadline extension. One rejected SPY expiry
remains excluded unless the user separately approves its exact retry.

Active finite run: 3945de19fd57d7962b641384; activation evidence and sanitized
logs are under .local/options-monitor-baseline-20260930/. Read status with:

```sh
PYTHONDONTWRITEBYTECODE=1 .local/options-monitor-runtime/venv312/bin/python -m quant_data.options.monitor.backfill --root /home/volatility/Python_Projects/Quant_Data_Infra status --run-id 3945de19fd57d7962b641384
```

Status does not initialize storage or contend for the execution-owner lock.
No complete seeded day existed at activation. Fresh independent verification
cleared retention, deadlines, lock coordination and the off-hours boundary;
provider session-collision diagnosis remains an inference.


September 30 throughput update (user requested ten concurrent workers): the run
command now defaults to `--workers 10 --api-concurrency 4`. This supersedes the
single-worker execution setting above; it does not change the frozen manifest,
remaining request/byte allowances, deadline, roster or failed-attempt rules.
A shared authenticated session supplies isolated worker channels. At most four
requests (including reduction and receipt persistence) are active; admission
permits also count pending requests so concurrency cannot extend the request cap.
Ticker-days execute concurrently within each priority-ordered ticker. Publication
runs on the coordinator after each ticker and flushes existing pending days at
startup. Raw rows remain ephemeral; only compact bins and receipts persist.

The backfill drains in-flight calls before closing channels, handles SIGTERM /
SIGINT without abandoning receipts, and renews authentication after a pause.
The parallel runner stops new calls at08:55 ET weekdays, giving the09:00 live
collector a drain margin, and resumes at17:00. It also yields19:10–19:40 ET
weekdays and02:55–04:05 ET Sundays around the
already authorized daily/weekly Theta jobs (19:15 / 03:00, bounded 20 / 60 minutes).
No recurring unit changes. Those fixed guards do not coordinate unrelated manual
Theta sessions on the account. Failed admissions are still not retried.

The installed SDK and official OHLC contract support `start_date` / `end_date`
within one month for a ticker/expiry. Multi-day request batching is not activated
in this change: its atomic admissions and coverage splitting need a separate
implementation to preserve the existing per-day no-repeat ledger. A synthetic
78,000-row reduction took 0.163 seconds locally; vectorization is not enabled.


September 30 full-expiry / multi-day experiment: the current volume bootstrap
uses the historical trade-contract catalog for each date, without max_dte or
strike_range filters, and enumerates every returned expiry. Non-traded expiries
contribute no trade volume. OHLC requests use all strikes and both rights.
The installed SDK's OHLC method requires a date-valued expiry (`strftime`);
an offline test proves `expiration="*"` fails before RPC. The official endpoint
also documents an explicit expiry date, unlike its all-strikes wildcard.

The user requested testing multi-day retrieval and vectorization. The separate
`baseline_probe` module is an experiment, not part of the production runner.
Its fixed scope is XLB August14 and17,2026, all discovered traded expiries,
40 data requests /128MiB /600 seconds maximum, one attempt, no retries. These
sessions precede the active bulk manifest. Four planned daily-control requests
(nearest/farthest common expiry, both dates) compare all78 five-minute bins
against the multi-day response. A synced exclusive ledger prevents reruns.
It retains only bins, coverage counts, digests, receipts and timings; raw rows
are discarded. No experimental observations/baselines are sent to the Site or
inserted into monitor.sqlite.

Live outcome:19 requests /182,869 bytes,13 distinct expiries through2028-01-21;
104/104 and138/138 catalog contracts were present in returned data. All four
daily controls matched their range-request aggregates exactly. Range requests
needed13 history calls versus24 for the equivalent separate daily history calls
(excluding2 catalogs and4 test controls). Across19,118 source rows, median
per-expiry processing times summed to0.053193s scalar and0.053261s NumPy.
A separate78,000-row synthetic comparison was0.254761s /0.254918s respectively.
The NumPy path keeps identical row validation and vectorizes integer reduction;
its conversion costs are included, with object-integer fallback for sums that
would overflow int64.

The two controlled range calls totaled9.352s versus3.201s for their four daily
controls. Controls ran second, so caching/request order may affect this result;
it is not a general provider-throughput claim. Correctness passed, but no speed
advantage was established. Production remains on single-day requests and scalar
aggregation with10 workers /4 active calls. Bulk collection resumed from all
2,691 saved receipts under its unchanged manifest/caps/deadline. No recurring
unit or WSL restart. See.local/options-monitor-batch-probe-20260930/summary.json,
live-admissions.jsonl,live-result.json and bulk-resume.json.


September 30 local-first recovery (latest user decision): collect, aggregate and
save the entire frozen backfill locally before starting Site publication. This
supersedes startup and per-ticker uploads in the throughput update above. A Site
read timeout during ABBV publication stopped the prior process after 937 saved
ticker-days; 907 were locally acknowledged as published. The uncertain POST
does not erase local aggregates or authorize another provider attempt.

Collection now loads neither Site configuration nor a Site client. A final
publication phase begins only when every planned ticker-day is saved or has an
explicit terminal gap (empty catalog or previously failed admission), with no
unresolved started admission. Status reports saved, skipped, pending and
unresolved days separately. Failed admissions remain non-retryable.

Final publication uses acknowledged chunks of 25 ticker-days, scoped to the
frozen run's symbols and sessions. A failed/uncertain POST leaves its chunk
pending and ends the phase without automatic retry. The explicit
backfill upload --run-id command resumes pending publication without constructing
a Theta client; it has the same completion gate and original deadline.
This is one final upload phase, not one unbounded HTTP request. No raw rows,
new schema, provider retries, recurring units or enlarged budgets are introduced.


September 30 continuous-queue optimization: the latest user request authorizes
optimizing the existing finite backfill. A 418.74-second receipt sample measured
2,433 requests and 1,481.57 summed RPC seconds (3.54 of four slots on average),
with 58.04 process CPU seconds. Provider latency dominates this sample.
Theta v3's subscription table lists four concurrent requests for Standard:
https://docs.thetadata.us/Articles/Getting-Started/Subscriptions.html

The scheduler now maintains at most ten unfinished ticker-days across the
priority-ordered roster. Worker threads and authenticated channels persist
between tickers; a slow day no longer creates a barrier before the next ticker.
Pause/fatal/finish paths drain all submitted work before channels close.
A pause preserves the deferred days and unsubmitted iterator, and renews
authentication before resuming. All finite plan caps, max-four request admission,
durable receipts, no-repeat rules and final-only Site upload remain unchanged.

The old and new scheduler completed an identical offline 96-ticker-day fixture
in 6.2417 and 5.9749 seconds respectively (1.045x); connection creation fell
from 80 to 10, with the same 192 calls and peak concurrency four. This synthetic
fixture establishes reuse and a modest gain, not a production speedup promise.
Forty focused baseline/transport tests passed without skips. The prior
multi-day/NumPy experiment remains unchanged and is not activated.


October 1 durable transient recovery: the user explicitly requested fixing error
handling and resuming after AFL/2026-09-10/expiry2026-10-23 returned UNAVAILABLE.
For the same frozen bulk run, this supersedes the earlier no-retry policy only
for known-byte UNAVAILABLE and DEADLINE_EXCEEDED responses. Each logical request
has at most three total attempts, including existing attempts, with 5- and
30-second retry delays. Repeated failures increase shared cooldown to at most
300 seconds. All calls drain before channels close and authentication renews.
After exhaustion, the failed day is an explicit gap and other days continue.
Authentication/permission, unknown-transfer, storage and overall budget failures
remain terminal. No completed request or successful ticker-day is downloaded again.

Recovery requires an immutable opt-in amendment for the exact run, stored under
recovery:<run_id> in baseline_runs. It preserves the original plan payload/id.
The existing baseline_requests table retains attempt 1 and appends #retry2/3
part identities. Logical resolution uses the latest attempt; historical
receipts are retained, and saved-day temporary payload cleanup includes retries.
DDL/version/checksum bytes are unchanged. All attempts consume the original
global request/byte allowances; unknown byte counts consume the entire byte
allowance. This is not a new allocation. Other-run failures, including the old
SPY pilot's UNAUTHENTICATED response, are excluded from this recovery policy.

Collection completeness now distinguishes retry-eligible pending days,
unresolved started attempts, exhausted/nonretryable gaps and saved days.
Final Site publication is blocked until collection is resolved.
The continuous ten-worker/four-call queue, full expiry coverage, daytime and
recurring-provider pause windows, and final-only Site upload remain in force.

A local baseline-status.json heartbeat records run/PID/time and explicit
collection, retrying, waiting, draining, stopped, upload_pending or complete
states. Active waits refresh at most every 30 seconds; market waits expose
resume_not_before. This file is diagnostic; SQLite receipts remain authoritative.
Primary recovery/baseline/transport validation passed 52 tests with no skips.
Activation follows fresh independent verification.


Independent review identified an admission-versus-failure-time edge case before
activation. Retry delay now starts from retained failure completion:
finished_at or captured_at plus RPC duration. Legacy receipts without reliable
timing receive the full delay from first inspection; restarts can only extend
that fallback wait. Original receipts are preserved. A regression with ten
seconds of authentication/setup followed by a one-second failed RPC confirms
the subsequent five-second wait. The corrected suite passed 53 tests, no skips.
The active heartbeat uses its own monotonic deadline and remains current when
futures finish frequently.


## October 1 daytime backfill and live priority

The user approved replacing the bulk run's market-hours stop with one
historical request at a time during weekdays 08:55-16:15 Eastern, then up to
four outside that window. The 16:15 boundary includes the existing late-close
ETF monitoring window. The finite run remains 3945de19fd57d7962b641384; original
scope, all-expiry coverage, request/byte allowances, deadline, durable retry
counts, saved-day identities and final-only Site publication are unchanged.
An immutable daytime:<run_id> amendment in baseline_runs opts the exact run
into this policy. It changes no schema or original manifest bytes.

The private admission module coordinates the singleton live-cycle and backfill
owners with OS-released flock leases under the existing private monitor root.
Live intent blocks new historical admissions immediately, waits for an already
active historical call and its receipt to finish, then holds exclusive provider
access for its cycle. Live concurrency remains three; backfill's existing
four-call semaphore and exclusive daytime capacity lease ensure no more than
four calls across these two owners, and only one historical call during the
daytime window. In-flight calls are drained, never cancelled or retried merely
because live collection becomes due. Waiting precedes durable admission.

Exclusive live-cycle access also prevents overlapping logins. A non-secret
generation marker changes before each live cycle; background workers detect
any intervening cycle, drain and close their old channels, and renew their
shared authentication before making another admitted request. This also covers
a complete live cycle between historical calls. No credential/token is written
to the coordination files. Process exit releases leases automatically.

Priority/capacity waiting respects original budgets, deadlines and cancellation.
The baseline heartbeat reports live_collection while yielding, separately from
provider backoff and the retained daily/weekly Theta job guards at 19:10-19:40
weekdays and Sunday 02:55-04:05. These guards still protect independently owned
daily and repair jobs; unrelated manual Theta sessions are not coordinated.

Normal timer-triggered live cycles pick up this gate without a manual trigger
or unit restart. No timer/cadence, recurring provider allocation, canonical
store, Site deployment or browser behavior changes. The current-turn primary
suite passed 100 offline tests without skips, covering cross-process priority,
drain/kill release, day/night capacity, authentication generation, cancellation,
replay/caps/deadlines, recovery and the affected live collector. An initially
timing-sensitive off-hours saturation assertion was replaced with a bounded
barrier that explicitly provides four simultaneous requests. Independent review
and bounded rollout evidence are recorded in the operating envelope.


## October 1, 2026 — today-only live pause for baseline priority

The user requested pausing live options monitoring for today and prioritizing
backfill. A private immutable live-pause-YYYY-MM-DD.json marker authorizes the
fixed matching backfill run to use up to four calls during that local day.
It includes its Eastern session date, exact next-local-midnight expiry, fixed
reason and run ID. It does not change any plan, schema, original allowance,
deadline, scope, retry count or recurring provider allocation.

A paused normal monitor cycle returns status=paused and requests=0 before store
initialization, credentials, Site access or provider work. It rechecks under
the existing live-owner lock to prevent provider admission if a pause arrives
after startup; setup already performed by an earlier cycle is not rolled back.
Activation requires an inactive timer AND an inactive service/MainPID=0 before
the marker is installed, so normal scheduled startup cannot overlap installation.
The dashboard
retains its last saved observations; live alerts and pushes do not run during
the pause. There is no retrospective live catch-up.

The same existing timer remains enabled and can tick with zero collection
during the pause. Date-indexed lookup stops applying the marker at the next
Eastern midnight, so normal clock operation resumes the following day without
a new automation or manual resume. The next day's daytime backfill limit is
again one request while live cycles retain priority. Other runs do not inherit
the priority boost. The daily/weekly Theta guard windows remain unchanged.

Installing the marker first requires the live timer to be paused and its
existing cycle to finish. Publication holds the existing live-owner lock and
atomically creates the mode-0600 marker without replacing prior authority.
Conflicting or malformed current markers fail closed. Historical markers are
retained as dated evidence; scheduled execution never creates or extends one.

The focused primary suite passed 109 offline tests with no skips, including
today's zero-external-work path, next-day automatic live admission, local/DST
expiry, immutable replay/conflicts, in-flight owner exclusion, malformed-marker
handling, four-call capacity and return to one without replay. Independent
verification and dated activation evidence belong in the operating envelope.


## October 1 evening — session setup recovery and receipt reconciliation

After the 17:10 ET stop, the user explicitly requested fixing and restarting the
same finite backfill. Session setup now preserves only sanitized error codes:
SESSION_TIMEOUT / SESSION_CONNECTION_ERROR for httpx timeout/transport failures,
AUTHENTICATION_REJECTED for SDK credential rejection, and SESSION_SETUP_FAILED
for other setup errors. The shared factory latches that category, so waiting
workers do not each log in. No exception body, credential or token is retained.

Before transport.request is invoked, a setup failure proves zero options-data
transfer. Its existing durable admission still consumes one request and attempt;
in-memory and durable counters now agree. Once dispatch starts, absent/unknown
receipts retain the full-allowance fail-closed charge. Authentication rejections
and unclassified setup failures remain terminal.

An explicit immutable session-recovery:<run_id> amendment opts the exact run into
setup recovery. Known transient setup failures share the existing three-total-
attempt limit, including prior data/setup attempts, with 30/120-second delays.
All active calls drain, then the factory renews once for the next cycle. Setup
retry exhaustion stops collection; it does not mark an outage as many empty days.
Existing data-RPC retry delays, concurrency, priority locks, guard windows and
deadline remain unchanged.

The same amendment can reconcile at most four individually reviewed legacy
RuntimeError admissions that occurred before dispatch. It pins each complete
original request-row fingerprint plus an external evidence digest. The reader
projects zero transferred bytes and an eligible setup outcome only for these
exact rows; originals are never rewritten/deleted, request counts never refunded,
and all other unknown transfers remain blocked. Changed rows fail closed.
The existing baseline schema/checksum and original run manifest remain intact.
Saved aggregates and successful partial responses are reused.

Focused validation covers typed/latching errors, opt-in and attempt limits,
restart timing, concurrent setup accounting, permanent rejection, deadline and
request exhaustion, preserved legacy receipts, tamper/other-run rejection, and
unknown dispatched failures. Activation and observed results are recorded in the
operating envelope. Site publication remains deferred until local collection
finishes.

Independent review caught the persisted-exhaustion path before activation.
Permanent or exhausted setup failures now remain explicit setup_blocked_days,
rather than terminal data gaps. Startup checks the entire run before admitting
any ticker, and direct upload remains blocked. A later manifest position or a
different terminal part in the same day cannot conceal the setup failure.
New offline restart/direct-upload regressions cover this boundary.


A later explicit user request, "Can you try again?", authorizes one additional
attempt for DD/2026-09-16/expiry2026-11-20 after its third attempt ended with a
known-zero predispatch SESSION_TIMEOUT. An immutable setup-retry-once:<run_id>
grant pins that exact parent fingerprint and permits only #retry4. It grants no
fifth attempt, cannot cover another part/run, and cannot be reset by replay.
Successful old requests remain reusable; all old and new attempts count against
the original global request allowance. Global bytes/deadline and retry rules for
every other part remain unchanged. Only known zero-byte timeout/connectivity
setup exhaustion is eligible; rejected credentials, unknown transfers and
non-setup failures cannot use this path. Absent/invalid grants reject attempt4.
Publication remains blocked if a subsequent setup attempt fails.


## Known-byte INTERNAL RPC recovery

The October 1 repair adds an explicit immutable internal-recovery:<run_id>
amendment for the already authorized finite backfill. It adds only INTERNAL
to the existing retry codes. The three-total-attempt ceiling includes all
prior attempts, with existing 5/30-second delays, request/byte/deadline limits,
coordinated draining and session renewal. Known nonnegative integer response
bytes and matching original/latest run ownership remain prerequisites.
Unknown transfer, rejected authentication and setup exhaustion remain blocked.

An amendment does not modify the original manifest, recovery/session policies,
request receipts, baseline schema/checksum or one-use setup retry grants.
Replay cannot replenish attempts or budget. Exhausted INTERNAL data requests
leave a visible unsaved gap; other ticker-days may continue. Successful cached
parts and saved days are reused. Retry-pending days block final-only upload.
Focused offline regressions cover these paths and policy tampering; exact
authorization and activation evidence are indexed in the operating envelope.


## Twenty-attempt recovery with separate connection accounting

An explicit immutable resilient-recovery amendment opts one finite run into
twenty total transient data attempts and twenty consecutive connection attempts
per outage. Previous contract attempts still count; earlier policies, manifests,
receipts, schema/checksum and the one-use setup grant are preserved. Unamended
runs retain their original rules. No global budget or deadline is increased.

Before workers begin, the coordinator records a separate login admission under
connection-start:<run>:<sequence>, then an immutable, hash-pinned
connection-finish record. Each consumes a unit of the existing global allowance
and carries zero options-data bytes. Connection failures do not reserve contract
attempts. Interrupted login admissions remain charged, count toward the failure
ceiling and observe cooldown after restart. Permanent rejection and the twentieth
consecutive failure block collection/upload; only successful login resets the
connection failure streak. Backoff grows through 30, 60, 120 and 300 seconds.

RPC retry drains channels but retains good authentication. Live intent, session
epoch changes and scheduled provider guards invalidate authentication under the
existing admission leases. No SQLite lock/transaction spans network work.
The completed-work shortcut still enforces unknown-transfer and budget guards.
A login finishing after the deadline cannot dispatch data. All finished
aggregates and successful partials are reused; publication remains final-only.


## October 2: short health reads and local save contention

Live health checks use .local/options-monitor-lock-recovery-20261002/health.py.
The original full postcheck is retained as dated audit evidence, not a live probe.
Receipt fingerprint sweeps use 500-row batches and compute hashes after releasing
the physical-store lock. Status counts empty successful catalogs directly but
still resolves failed/retried catalogs, setup blocks, and connection state.

A completed response stays in memory through up to three exact lock-acquisition
timeout retries when persisting its existing reduced result. No new provider
admission occurs for those local waits. Other store errors remain fail-closed.
An explicit immutable storage-retry grant can hash-pin a measured successful
response lost at the local save boundary, authorizing only its next attempt
within the original run ceiling. Prior receipts/caps/schema remain immutable.
Activation and scope evidence live in the operating envelope.


The October 2 repair was independently verified and activated for run
3945de19fd57d7962b641384 at 14:45:55Z. Live acceptance confirmed new successful
requests, new saved days, and successful second-attempt recovery of the precise
SKHY local-save failure. Old receipt/day hashes and policy values remained intact.
The hourly automation now uses the bounded health reader. See the operating
envelope's verified-activation entry and local live-acceptance.json for dated totals.

## October 2 evening — four interrupted UAA admissions

Following the presented four-request byte-accounting exception, the user's
'resume it' accepts that exact scope for run 3945de19fd57d7962b641384.
An append-only interrupted-recovery amendment pins the incident fingerprints
for UAA session/expiry pairs 2026-09-10/2027-03-19, 2026-09-09/2027-01-15,
2026-09-04/2026-12-18, and 2026-09-01/2027-01-15. Their original rows remain
started with null completion receipts and unknown response bytes. The four
unknown byte amounts are excluded from budget accounting; they are not
recorded as zero-byte responses. Original admissions and attempts remain charged.

Only these original expiry admissions may advance to attempt 2 under the
existing 20-total-attempt policy. Later attempts use ordinary accounting.
A new interruption, any unrelated unknown transfer, changed parent fingerprint,
or conflicting amendment remains blocked. Existing request/byte ceilings,
deadline, worker slots, provider guards, live priority, and final publication
policy remain unchanged. Completion examines the latest logical attempts while
reporting the four excluded original unknown requests explicitly.

Activation requires the existing finite-run owner lock, unchanged incident
parents/manifest/policies/saved counts, focused offline checks, and fresh
independent verification. No schema change or raw-response retention is added.

## October 3 — explicitly requested SPY authentication retry

A manual auth-retry-once grant pins a known-byte UNAUTHENTICATED original expiry
receipt and permits exactly attempt 2 under one specified receiving run. This
supports the approved SPY September 29 repair whose original failure belongs to
the earlier pilot. The original row/run and receipt stay unchanged; new requests
are charged to the receiving run within its existing limits. Both plans must
contain the exact symbol/session. The grant is immutable and idempotent; replay,
later transient errors, or interrupted retry do not grant attempt 3.

This is an operator-approved single request, not an automatic authentication
recovery policy. The targeted one-pass helper limits execution to the saved
catalog's complete expiry set, reuses successful aggregates, admits at most one
login and one request per missing expiry, and publishes only the complete day
through the existing acknowledgment path. It makes no credential changes.


## October 8 — Inspector Intraday Options health

The local Inspector Status page includes an IO (Intraday Options) marker on
each calendar day and the matching Intraday Options card inside the selected-day
right panel, before the daily batch timeline. Intraday health remains outside
daily batch-slot totals. Its host binds the existing private monitor store; the
HTTP route still accepts only a date. It reads saved observations, request
outcomes, calendar metadata, pending upload/alert counts and the existing
monitor timer/service properties. It does not fetch provider data, initialize
or modify stores, change schedules, or contact the Site.

Current operating health is independent of the selected historical calendar
date. During the trading session, every configured symbol must be either within
its saved 5/10/15/30-minute cadence plus a five-minute allowance or inside its
initial scheduling allowance. Missing and overdue symbols prevent a healthy
summary. Recorded requests without completion become unresolved after five
minutes. Market-closed periods and explicit per-session pauses are neutral
states; missing or incomplete evidence is unknown, never zero or healthy.
Request history older than its retained window remains unconfirmed.

Reads use MonitorStore's immutable connection under its physical-store lock,
with a 250 ms lock wait and a two-second / ten-million-operation SQL budget.
Current health is read before optional historical detail; a historical scan
that exhausts the remaining budget leaves current health visible and the
historical summary unknown. No baseline receipt-history sweep or raw payload
read is required. The snapshot updates on page refresh.

Dashboard delivery is the local acknowledgment queue only. Pending observations
and alerts are shown separately; a clear queue does not establish the remote
Site's current contents, and no successful-upload time is invented from an
attempt timestamp. The 5/10/15/30-minute breakdown uses each symbol's latest
saved cadence and labels the last observation in Eastern time.


### Calendar integration

Calendar markers summarize retained request outcomes for their own date.
Successful requests (including no-data responses) are labeled Recorded, not
proof of full-session observation coverage. Failed and unfinished requests
remain visible after the market closes. Older absent request history is
Unconfirmed; future weekday collection is only Planned, subject to the market
calendar. A historical or future day card never presents today's freshness,
cadence state or pending upload queue as evidence for that selected day.

A separate immutable, physical-lock read scans only compact retained request
metadata for the displayed month, capped at three seconds and twenty million
SQLite operations. Empty months use the session index before any scan; no
baseline receipt ledger or raw option payload is scanned. The current snapshot
and calendar projection fail independently to unknown when busy or incomplete.
Both remain snapshots refreshed with the page; their counts do not inflate the
existing daily batch-slot summary.

The right-panel card uses a compact summary by default, matching the daily
batch cards. Collection and local dashboard-delivery states remain visible;
Run details expands the exact timestamps, saved counts, cadence breakdown and
supporting evidence. Historical selections keep their date-specific summary
and details without showing current-day delivery or freshness.


## October 9, 2026  volume-based interval eligibility

The user approved relaxing both total and active distinct-contract count gates
and requested implementation. This supersedes only the count-based rejection
rule in the September 28 full-chain interval decision. A successful full-chain
provider response remains the completeness premise; changes to returned series
can include inactive/prior-session rows and do not by themselves invalidate a
cumulative-volume difference.

New observations retain diagnostic flags `contract_count_decreased`,
`active_contract_count_decreased`, and `coverage_counts_unavailable`, but
these flags do not block interval volume, RVOL, baseline participation, or the
existing alert rules. Each side's cumulative volume must remain nonnegative
and nondecreasing. Missing/incomplete volumes, invalid/future/cross-session
times, and corrections remain blocked. Collection gaps and cadence changes
retain descriptive interval volume but remain ineligible for RVOL and alerts.
At least 20 matched prior sessions and a positive baseline median are required
for RVOL. Existing alert thresholds and other alert-quality gates are retained.

An optional `baseline_available_sessions` aggregate counts eligible prior
samples supplied for the scheduled window before the current interval is
validated. `baseline_sessions` continues to count samples matched to the
usable actual interval. The Site presents history availability separately from
current interval rejection, missing snapshot pairs, window mismatch, warm-up,
and a zero/unavailable baseline median. Historical observations without the new
field do not imply zero available history when their interval was rejected.

Existing `contract_coverage_lost` and `coverage_comparison_unavailable`
flags remain blocking for retained observations. No saved observations or
historical baselines are rewritten. The optional field is accepted by the Site
before the collector begins publishing it; the existing bounded local agent
reader projection retains its documented field set.

This policy accepts that omitted traded series can escape detection when other
trading keeps both cumulative totals increasing, and their reappearance can
inflate a later interval. Compact aggregate retention cannot prove membership
continuity. The change introduces no provider requests, retries, recurring-unit
configuration changes, or historical recollection. It is applied to future
normal collector executions after the compatible Site release. Current Site
sharing is preserved.

Scoped validation and activation evidence is recorded in
`.local/options-count-gate-20261009/`. The collector candidate is tested in
isolation before replacing the unchanged active module; release evidence is
recorded only after the actual checks and deployment complete.

### Validation and pending rollout

The 96 distinct scoped backend tests passed after one obsolete inactive-row
removal expectation was updated (initial 95/96; affected 42-test recheck passed).
The Site passed 166 presentation assertions, 14 local API assertions, TypeScript,
production build and scoped diff checks. Actual CUA browser checks covered mixed,
rejected and warming fixtures plus ticker details, with no JavaScript errors.
The UI design owner reviewed four saved desktop/mobile screenshots successfully.
A fresh independent verifier passed 12 pure interval tests and 35 additional
boundary assertions, independently repeated the 166 Site assertions, and found
no blocking issue. No live provider call was made.

Site source 43a0d0c4ec51f0c09eb02ce2e9a1d82da3eb66b0 is pushed and saved as
version 10. Automatic approval review rejected production deployment because
implementation approval was not accepted as explicit external-release authority.
No deployment occurred and no collector module was activated. The new model
and changed tests remain staged in the evidence directory; active source and
its default tests preserve pre-change behavior. Exact release IDs and fingerprints
are in release.json, independent-review.json, candidate.json and staged-tests.json.
Activation awaits the requested explicit deployment/collector approval.


### October 9 production release and activation

After explicit user deployment approval, saved Site version 10 (source
43a0d0c4ec51f0c09eb02ce2e9a1d82da3eb66b0) deployed successfully at
2026-10-09 16:49:40 UTC, deployment
appgdep_6ac91b1be1fc81919b8a18b5a827c722. The production URL is
https://quant-options-monitor.yanggainan.chatgpt.site; existing sharing was
preserved. The exact reviewed model and test candidates were installed at
16:51:10 UTC, with all preimage and installed-candidate fingerprints verified.

The policy applies to future normal scheduled process executions. Existing
rejected history remains unchanged. No manual collector run, provider request,
recurring-unit change or historical rewrite occurred; a post-activation scheduled
scan has not been observed as part of this release. Previously completed scoped
tests, browser checks and independent review apply to these identical installed
bytes. See .local/options-count-gate-20261009/activation.json for the actual
deployment response, activation time and installed-file hashes. The prior pending
rollout section and release.json preserve the earlier approval-gate history.


## October 9: cumulative intraday volume alert

The user requested an additional alert based on intraday cumulative volume to
reduce dependence on individual collection intervals. The existing interval
alert remains. The new cumulative signal compares today's complete call + put
volume since the open against the median volume reached by the same Eastern
cutoff in the latest 60 distinct eligible prior sessions (at least 20 required).
The initial threshold is 3x, with at least 500 current-session contracts and a
positive expected median. No interval acceleration, IV rise, or concentration
support is required for this independent rule. Watchlist alert opt-out applies
to both kinds. Cooldown is 30 minutes per ticker and alert kind, so one kind
cannot suppress the other; same-observation dual alerts have distinct IDs.

Historical seeds sum completed half-open five-minute bars from 09:30 through
the scheduled cutoff. Actual live snapshot time must be within one minute of
that cutoff. Early-close seeds cannot supply later cutoffs. The reader combines
seeds and complete same-time saved snapshots within the existing 130-day bound,
uses eligible live data first per session, and excludes current/future sessions.
There is no end-of-day scaling, interpolation, future bar, or historical fetch.
Missing scans, cadence changes and a first current-session snapshot do not by
themselves invalidate a complete cumulative total. Missing/incomplete volume,
invalid/future snapshot times, detected per-side cumulative corrections and
legacy coverage rejections remain excluded. Underlying inconsistency retains
the existing alert-only rejection. Relaxed contract-count diagnostics remain
informational. Upstream omitted traded series may still distort totals; compact
aggregates cannot independently prove full chain membership.

New optional observation fields are cumulative_volume,
cumulative_relative_volume, cumulative_expected_volume,
cumulative_baseline_sessions, cumulative_baseline_minute (Eastern minutes after
midnight), and cumulative_quality_flags. New cumulative alerts carry
kind=cumulative; absent kind continues to mean the original interval alert.
The existing SQLite schema/checksum is unchanged. Observation, up to two alerts,
and their outbox records commit together with replay and rollback protection.
The public agent-reader projection retains its existing documented field set.

The inbox labels alert kind and uses the original saved observation for its
evidence. Cumulative cards show session-to-date volume, expected cumulative
median, RVOL, prior-session count and cutoff. Ticker detail shows a separate
latest-snapshot cumulative comparison; alert navigation selects the matching
existing chart mode. Old observations missing these optional fields remain
unavailable rather than appearing to have zero cumulative volume or history.

Implementation evidence is staged in .local/options-cumulative-20261009/.
The compatible Site must be deployed before activating the staged collector
modules for future normal scheduled executions. This implementation performs
no provider request, manual collector run, recurring-unit change, historical
rewrite, or historical alert generation. Actual deployment/activation evidence
is appended only after successful release.

### Cumulative alert validation

117 distinct backend tests passed (21 new cumulative plus 96 monitor, interval,
baseline and public-reader tests). The independent reviewer reproduced the
affected 63 tests after a temporal-gate correction and retained the earlier
54 baseline/public-reader passes. Fifteen additional independent checks covered
time ordering, same-time tolerance, retention bounds, live/seed precedence,
read neutrality and rollback if the second alert insert fails. The initial
review found that invalid interval time/identity flags needed to block cumulative
eligibility too; both flags now block calculation, alerts and future baseline
participation. The original report and correction evidence are retained.

The Site passed 235 UI assertions, 22 local API assertions, TypeScript and the
production build. Actual CUA desktop/mobile checks verified original cumulative
evidence versus latest-snapshot values, both alert chart modes and zero JavaScript
errors. The UI design owner reviewed four screenshots with no visual blocker.
All API/store fixtures were local and isolated; no provider requests, real
notifications or operational-store access occurred. Exact source fingerprints,
logs and independent review are in .local/options-cumulative-20261009/.

### Cumulative release saved; deployment approval pending

Site source 89643713a7cc1c5bed46c7618e7e564635cfba29 is pushed and saved as
version 11. Automatic approval review rejected production publication because
the new cumulative-alert release lacks explicit deployment approval. Approval
to deploy version 11 and activate the reviewed collector has been requested.
The compatible Site has not been deployed, all four active backend files still
match their preimages, and the new cumulative test remains staged. Existing
live behavior and history are unchanged. Exact saved-version identity, archive
fingerprints, rejection and activation status are retained in
.local/options-cumulative-20261009/release.json.


### October 9: version 11 deployed and cumulative alerts activated

The user explicitly authorized deploying version 11 and activating cumulative
alerts. The saved version deployed successfully at 2026-10-09 20:49:56 EDT,
deployment appgdep_6ac98babe1c0819182043bce04449240, source
89643713a7cc1c5bed46c7618e7e564635cfba29, at
https://quant-options-monitor.yanggainan.chatgpt.site. A post-deployment read
confirmed the existing custom sharing policy remained identical (revision 4).

At 2026-10-09 20:50:34 UTC-04:00, the four reviewed collector modules and new
cumulative test were installed with exact preimage and candidate hashes checked.
Dependencies were installed before their consumer. All installed files match
the independently reviewed bytes; the 21 cumulative tests also passed against
the installed code using temporary offline fixtures. Prior broader validation
remains applicable to these unchanged bytes.

Cumulative alerts now apply alongside interval alerts in future normal scheduled
processes. No manual collector run, provider request, recurring-unit change or
historical rewrite was performed. No post-activation live scan is claimed.
The actual native deployment response, activation timestamps, installed hashes,
sharing check and test result are in .local/options-cumulative-20261009/activation.json.
The earlier release.json and approval rejection remain historical evidence.

## Grouped Inbox and Activity views — October 9 follow-up

The user accepted a ticker-first radar with separate Activity and Inbox views.
Activity shows 20 instruments per page, ranks usable latest-session cumulative
RVOL by default, and displays interval RVOL, session volume and freshness
separately. Valid cumulative comparisons remain available when interval
comparisons are unavailable. Stale or older-session observations cannot rank as
current activity. Market and Watchlist retain their existing purposes.

Inbox groups saved alerts by Eastern trading date and ticker. The session
selector, needs-review/all filter and search operate over all retained alerts;
20-group pages and their totals are computed on the server, independently of
the legacy latest-200 dashboard response. Each kind's displayed ratio and time
come from its latest saved alert and original observation. Missing original
evidence remains missing. Complete current-session confirmed symbols and
retained-wide unreviewed ticker/event totals supplement the dashboard response.

Opening a group selects a saved event and offers a collapsed, 50-event paged
timeline. Selected-alert evidence is distinct from the latest snapshot. Review
state remains shared. Same-origin authenticated review accepts at most 50
explicit displayed event IDs; arrivals outside that set remain unreviewed and
return their ticker to Needs review. The existing one-ID review remains valid.
No schema migration, collector policy, provider workload or recurring-unit
change is part of this presentation update.

Focused validation and release status are recorded in
.local/options-grouped-inbox-20261009/. Browser verification and production
release must be recorded separately from successful source/build checks.

Version 12 subsequently passed actual desktop/mobile browser checks and the
configured design owner's visual review without source changes. It deployed
successfully at 2026-10-09 22:35:51 EDT as
appgdep_6ac9a480262c8191b16f1ff0c5b78a26. Published Inbox and Activity views were
read-only verified; custom sharing revision 4 remained identical. Full release
and browser evidence is in that directory's activation.json. Synthetic batch
review preserved a concurrent arrival; no production review state was changed.

## Market-cap heatmap — October 10 follow-up

The user requested a Universe radar heatmap with company market cap controlling
box area and intraday cumulative relative volume controlling color. This adds
a Heatmap view alongside Inbox and Activity; existing collection and alert
policies retain their meaning.

A bounded locked immutable audit of the 2,248 selected retained FMP profiles
found positive `marketCap` values for every company, with capture dates from
September 10 through September 22. These are dated saved profile values, not
live market caps or historical point-in-time reconstructions. Sector/industry
labels come from the canonical FMP classification mapping and retain that
source's meaning. Exact stored provider symbols map the nine share-class aliases
to the frozen monitor roster; no punctuation-based identity guess is used.

The dated Site metadata contains 2,248 mapped companies. Of the 2,251 radar
equities, 2,247 have positive USD caps usable for proportional sizing. AVB, EA
and EQR have no matching retained profile; IVA's profile reports EUR. Those
four names and the 15 ETFs are explicitly excluded from market-cap sizing;
no FX conversion, ETF AUM substitution, inferred cap, or minimum tile size is
applied. Values are provider-reported per ticker; multiple share classes are
not asserted to represent disjoint issuer capitalization.

The existing authenticated dashboard response supplies the compact reproducible
metadata asset. It preserves per-ticker source timestamps and a hash of the
validated source manifest. This release exports one saved snapshot only; it
adds no recurring canonical-store read, provider fetch, schedule, live refresh,
canonical migration or database write. A later market-cap refresh is separate
work. The offline export helper is `sites/options-monitor/scripts/build-heatmap-metadata.py`;
audit inputs, counts and release evidence are in
`.local/options-heatmap-20261010/`.

Heatmap colors reuse current Activity cumulative-readiness/freshness semantics.
An unavailable interval comparison does not suppress an otherwise usable
cumulative comparison. Older, overdue or ineligible cumulative comparisons
remain neutral; missing RVOL is not zero. Sector navigation and ticker search
provide access to boxes too small to label. Color changes do not change the
cap-based layout or imply buying, selling, or price direction.

Version 13 deployed successfully at `2026-10-10T15:03:43.803886Z`, source
`4e5ff07ae7a7d1df7fd22b11d4aecf93bffd6f49`, deployment
`appgdep_6aca53c7a2688191bf28ce3a4eab8bf3`. The complete custom sharing policy
remained identical at revision 4. Focused checks passed: seven metadata tests,
12 heatmap API assertions, 22 cumulative API regressions, 6,884 heatmap UI
assertions, 50 radar assertions, TypeScript and the final production build.
Fresh independent offline/data/geometry review found no blockers; actual desktop
and mobile browser checks and the configured design owner's visual review passed.
The published view was read-only verified with 2,247 mapped equities and zero
usable cumulative comparisons in the then-saved observations; all tiles correctly
remained gray. Browser logs contained no warnings or errors. Full release identity,
scope, validation limitations and screenshot references are in
`.local/options-heatmap-20261010/activation.json`.


### October 10: cumulative-alert-only heatmap

The user narrowed heatmap membership to tickers with a saved cumulative alert.
Version 14 filters the complete distinct cumulative-alert symbol set for the
current radar session, including reviewed alerts. The existing collector/latest
session rule is reused; older-session and interval-only alerts do not qualify.
Membership is independent of the legacy latest-200 feed and current RVOL levels.
The filter applies before cap sizing, sector options, search and coverage counts.
Missing membership fails closed; no alerts produces a dated empty state.
Market-cap snapshot, proportional sizes, exclusions and current-RVOL freshness
rules are unchanged. Existing safety, authentication and mutation contracts are
unchanged; no canonical reads/writes, providers or recurring-unit changes occur.

Version 14 deployed at `2026-10-10T19:38:27.344240Z`, source
`a641206c5724d2cfdb4a714e72fd7d7a2851078d`, deployment
`appgdep_6aca942bc1d0819193613beaf4cc1ef4`. Complete sharing remained unchanged
at revision 4. Focused SQL, actual temporary API, UI, TypeScript, build and
browser checks passed. The configured design owner passed three screenshots,
noting a minor nonblocking badge overlap in one mobile synthetic tile.
Published verification showed the correct no-cumulative-alerts state for October
9. Receipt, precise check counts and limitations:
`.local/options-heatmap-alerts-20261010/activation.json`.
