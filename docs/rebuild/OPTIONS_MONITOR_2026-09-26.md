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
