# Scheduled refresh unit guidance

Status: Current unit guidance. Unit-file presence alone does not authorize
linking, starting, enabling, disabling, updating, removing, or manually
triggering a service or timer. Before scheduler or provider work, read the
[current operating envelope](../../docs/rebuild/CURRENT_OPERATING_ENVELOPE.md)
and [scheduling contract](../../docs/rebuild/SCHEDULING_AND_LOCKING.md).

Existing recurring units retain their documented state. The Stage 12E
market-close timer is enabled and waiting as recorded below. Agents may inspect
status read-only, but must not manually trigger, retry, broaden, disable,
update, remove, or repurpose any unit.

## Active: market-close timer

On 2026-08-29 the reviewed project-local
`quant-data-market-close.service` and `quant-data-market-close.timer` were
daemon-reloaded, enabled, and started. The timer's verified state is
`LoadState=loaded`, `UnitFileState=enabled`, `ActiveState=active`, and
`SubState=waiting`; its next trigger is Monday 2026-08-31 18:00:00 EDT and
`LastTrigger` is empty.

The service remains `inactive/dead` with no
`ExecMainStartTimestamp` or `ExecMainExitTimestamp`. No state directory,
provider request, or canonical write occurred during activation. It runs at
18:00 America/New_York on weekdays with `Persistent=false`, so there is no
catch-up. It has no hidden retry.

Its zero-argument service snapshots every current FMP provider-native
`stage10_instruments` identity whose `asset_type` is `equity`, `etf`, or
`index`. The 2026-08-29 preflight contained 630 identities (519 equities, 96
ETFs, and 15 indexes); the dynamic batch is bounded to 800 and ordered `AAPL`
first. It makes one current-session FMP daily-OHLCV request per symbol; an
empty `AAPL` response is the no-market-session sentinel and stops the batch.
The ten pinned historical noncoverage symbols remain eligible: valid 200 data
is published if it becomes available, only their exact reviewed empty/HTTP 402
outcomes are terminal, and other missing or error outcomes fail closed.

The service writes only through the fixed canonical `data/market.sqlite`
publisher; exact semantic replay writes nothing, and per-symbol evidence is
private and durable. The completed Stage 12C provider write remains no-repeat,
and the frozen Stage 7 `market-close` job remains disabled.

These read-only inspection commands apply:

    systemctl --user status quant-data-market-close.service
    systemctl --user status quant-data-market-close.timer
    systemctl --user list-timers quant-data-market-close.timer --all

Do not manually start the service, expose a credential, or repurpose the timer
for another store, cadence, universe, or historical range.

## Active GDP/CPI vintage timer

The macro-vintage timer is enabled for 09:05 America/New_York on weekdays. It
refreshes only the official BEA GDP workbook and the two-series BLS current API
request. It has no credential or retry, performs no migration, and does not
repeat the completed 14-file BLS archive backfill.

The manual service gate completed as two semantic no-ops before recurrence was
enabled. These inspection commands are read-only:

    systemctl --user status quant-data-macro-vintages.service
    systemctl --user status quant-data-macro-vintages.timer
    systemctl --user list-timers quant-data-macro-vintages.timer --all

Do not manually start the service or change the timer.

## Active employment-vintage timer

The separate employment timer is enabled for the first Friday of each month at
10:05 America/New_York. Its recurring path requests only the current BLS payroll
and unemployment series. It has no credential or retry, performs no migration,
and never repeats the completed Philadelphia Fed historical workbook backfill.
It is non-persistent, so missed windows are not replayed automatically.

Inspect the installed units with:

    systemctl --user status quant-data-employment-vintages.service
    systemctl --user status quant-data-employment-vintages.timer
    systemctl --user list-timers quant-data-employment-vintages.timer --all

The completed manual outside-window service check returned `requested=0` and
made no provider request. The historical backfill is manual-only and must not
be invoked again.

Do not manually start the service or change the timer.

## Active FMP macro-calendar timer

The FMP macro-calendar timer runs at 08:15 and 08:45 America/New_York on
weekdays. Each invocation makes one bounded current-window FMP calendar
request with no retry and retains wholesale raw evidence before independent
GDP/CPI and employment normalization. It does not repeat either completed
historical 56-window run.

These inspection commands are read-only:

    systemctl --user status quant-data-fmp-macro-calendar.service
    systemctl --user status quant-data-fmp-macro-calendar.timer
    systemctl --user list-timers quant-data-fmp-macro-calendar.timer --all

Do not manually start the service, change the timer, import a credential for a
different operation, or install another recurring unit.

## Active aggregate macro-current timer

The aggregate macro-current timer runs at 18:30 America/New_York on weekdays.
It sequentially refreshes the twenty-three established Treasury-curve,
rates, liquidity, industrial-production, financial-conditions,
national-activity, credit, Treasury-cash, Treasury-debt,
federal-fiscal-balance, gas-storage, weekly crude-oil, gasoline, and distillate
stocks, finished-gasoline product supplied, electricity-retail, recession-
chronology, BLS price/wage/productivity, and BEA personal-income/outlays
operations. ECI rides the existing BLS request; CFNAI and INDPRO add one
request each. Its total provider-request cap is 31 per
invocation, it does not
retry, and unchanged
content causes no canonical write. It is non-persistent, so missed windows are
not replayed automatically. The existing GDP/CPI, employment, and FMP-calendar
timers retain their separate cadences.

These inspection commands are read-only:

    systemctl --user status quant-data-macro-current-refresh.service
    systemctl --user status quant-data-macro-current-refresh.timer
    systemctl --user list-timers quant-data-macro-current-refresh.timer --all

Do not manually start the service or change the timer.

## Active Alpaca SPY option-surface timer

The fixed Alpaca timer is enabled for 15:55 America/New_York on weekdays. Its
first scheduled trigger is 2026-08-25 at 15:55 EDT.

It is non-persistent, has an in-process OPRA trading-calendar gate, makes at
most four single-attempt requests, and writes only to the existing option
tables in `data/market.sqlite`. It does not enable either recovered market
job.

These inspection commands are read-only:

    systemctl --user status quant-data-alpaca-spy-options.service
    systemctl --user status quant-data-alpaca-spy-options.timer
    systemctl --user list-timers quant-data-alpaca-spy-options.timer --all

Do not manually start the service, change the timer, expose credentials, or
repurpose it for another symbol, feed, environment, time, or store.

## Authorized SEC market-equity fundamentals timer

`quant-data-sec-company-fundamentals.timer` is enabled for 07:15
America/New_York on weekdays with `Persistent=false`. Its fixed zero-argument
service reads the canonical Stage 10 equity roster, performs one bounded SEC
ticker discovery, and processes sequential submissions/CompanyFacts pairs at
no more than five requests per second, without retry. It targets only
`data/company.sqlite`; `data/market.sqlite` is descriptor-pinned and immutable
for roster discovery. It was linked and enabled on 2026-08-25 after the
authorized historical population, focused validation, independent review,
and host checks passed. The service was not manually started and its first
scheduled trigger is 2026-08-26 at 07:15 America/New_York. Do not manually
start or repurpose the service or the frozen recovered `sec-daily` job.
