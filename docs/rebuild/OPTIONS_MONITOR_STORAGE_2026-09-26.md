# Options Monitor storage and watchlist limits — September 26, 2026

## Accepted scope

The user selected 25 tickers per authenticated user, a site-wide cap of 100 distinct watchlist tickers, and rolling 90-session intraday retention. Collection is shared by symbol, using the fastest requested cadence. The full 2,251-equity plus 15-ETF scanner remains in scope; the watchlist quota does not reduce that roster. No provider limits or scheduler settings changed.

Daily ETF history belongs in `data/options.sqlite`, with derived calculations where inputs support them. Intraday observations and monitoring belong in `data/.operations/options-monitor/monitor.sqlite`. The Site holds a derived serving cache. This update does not add the historical chart adapter or a recurring EOD updater. The current daily options collection covers 15 ETFs, not historical daily options for the single-name universe. A five-year operating horizon does not imply five-year intraday retention.

## What is retained

- Ninety recorded sessions: per-symbol timestamp/cadence, cumulative call/put volume, valid interval volume, trade count, IV estimates and changes, put/call balance, volume/OI and its effective date, maturity/concentration shares, quality/coverage, and at most five leaders. These support charts, valid same-time baselines, and explaining anomalies.
- Alert records and associated push-deduplication state follow the observation retention window. The alert inbox/review state remains shared across site viewers; watchlists and per-ticker push opt-outs are personal.
- Seven recorded sessions: per-request attempt and sanitized receipt records. Current-session accounting and replay admission remain available. No provider raw responses are stored.
- Unacknowledged deliveries are retained within the existing observation window. Acknowledged delivery payload copies are removed immediately; observation identity still prevents replay.
- The cloud cache holds at most 90 observed sessions, with its existing 130-calendar-day age ceiling. A tiny session table supplies the cutoff without repeated full-history scans. Latest values and alerts expire with the cache.
- Session calendars are small; yearly calendar metadata grows by roughly one small entry per year. No new perpetual per-request log was introduced.

The existing shared watchlist was verified empty in production before adding `user_watchlist`. The additive migration preserves the old empty table. Each request scopes reads, edits, and deletes to the platform-authenticated user ID. One atomic insert/update enforces both limits even under concurrent additions. Updating an existing selection and joining an already-shared ticker remain possible at the global cap when the user's own quota permits it.

## Audit and sizing evidence

Locked immutable audit at 2026-09-26T23:24:57Z: the operational monitor database was 77,824 bytes (76 KiB), with zero provider attempts, observations, alerts, and deliveries. It has not yet accumulated trading-session data. This is an observed current size, not a production growth benchmark.

A temporary synthetic benchmark populated the actual SQLite schema with 39,000 rich snapshots and a separate 39,000 radar snapshots (100 symbols × 78 snapshots × 5 days), including five leaders and realistic numeric precision. SQLite page allocation includes table/index overhead. Raw inputs, live providers, and credentials were not used. Evidence and the reproducible benchmark are in `.local/options-monitor-storage-review/`.

Measured average allocated bytes per rich snapshot: local observations/indexes 2,110; operational logs at two requests per snapshot plus daily OI 1,436; cloud history/indexes 1,433. Radar observations used about 1,478 local bytes, with 663 log bytes and 1,433 cloud bytes. Actual payload sizes and page packing vary. The log forecast applies seven sessions, while observation forecasts apply ninety.

| Cadence, all 100 tickers | Snapshots/session | Local monitor, 90 sessions | Cloud cache, 90 sessions | Hypothetical local archive of all five years |
| --- | ---: | ---: | ---: | ---: |
| 5 minutes | 7,800 | 1.56 GB | 1.01 GB | 20.82 GB |
| 10 minutes | 3,900 | 0.78 GB | 0.50 GB | 10.41 GB |
| 15 minutes | 2,600 | 0.52 GB | 0.34 GB | 6.94 GB |
| 30 minutes | 1,300 | 0.26 GB | 0.17 GB | 3.47 GB |

Assumptions: decimal GB, 390-minute regular session, 252 sessions/year, 5 years = 1,260 sessions, one shared stream per unique ticker. Opening/closing boundary samples and extended ETF trading add a little overhead. Hypothetical five-year archival numbers still retain only seven sessions of request logs; they are not the selected policy. At five minutes, 9.828 million snapshots are produced over five years but only about 702,000 remain in the rolling window.

Plan around 2–3 GB local plus 1–2 GB cloud for 100 rich tickers at five minutes, before backups. A single local backup adds roughly another database-sized allocation. These are capacity estimates, not configured byte ceilings or hosting-cost quotes. They exclude existing `options.sqlite`, new single-name EOD history, unusually large alert/outage backlogs, and long-term backups. SQLite normally reuses freed pages; pruning does not promise that the file immediately shrinks.

For the whole current roster, an illustrative mix of 100 watchlist names at 5 minutes, 100 different liquidity-tier names at 15 minutes, 2,051 other equities at 30 minutes, 11 sectors at 10 minutes, and four broad ETFs at 5 minutes yields approximately 5.74 GB local plus 4.88 GB cloud. Allow roughly 8 GB local and 7 GB cloud before backups. Overlap between the watchlist and liquidity tier reduces this; anomaly promotions can increase it. These are workload projections, not proof that every scheduled scan meets the provider's throughput budget.

## Validation

- 18 focused collector tests passed with temporary stores and offline transports, including 90-session/7-session retention, current-session admission preservation, acknowledged-delivery replay, and personal push opt-outs.
- Three exact-SQL temporary-database tests passed, covering concurrent personal/global caps, updates/sharing at capacity, user isolation, and fastest-cadence merging.
- 98 local API acceptance assertions passed, including authenticated mutations, the personal cap, the 90-session cache and prior publication/security regressions.
- Local Chromium exercised add/save/remove, alert preference, the existing chart, notification readiness and mobile overflow with no JavaScript exceptions. Desktop watchlist capacity text was visually inspected.
- TypeScript check passed. Final build/publication evidence is recorded separately in the monitor contract after successful publication.

No existing canonical database was changed. No live collection, historical refetch, unit restart or timer change was performed.


## Implemented historical chart cache — subsequent follow-up

The later explicit implementation request adds daily ETF charts from the existing
canonical store. Daily projections are served by the private Site's new
`daily_history` table; none are added to `monitor.sqlite`. This is a disposable
serving cache with source capture/hash/version metadata, not a second canonical
history store. It is maintained from existing saved facts, with zero historical
provider requests. It does not change the 90-session intraday retention policy.

A temporary representative benchmark of the exact daily table/index schema and
37,863 projected rows allocated 27,324,416 bytes (27.3 decimal MB). This is a sizing
estimate, not a measured cloud allocation. Allow approximately 30–40 MB for this
initial daily cache; five further years for 15 ETFs at 252 sessions/year adds
roughly 14 MB at the measured average row size. Actual payloads and indexes vary.
The original intraday estimates above exclude this small daily serving cache.
Evidence: `.local/options-monitor-history/cache-size-estimate.json`.
