# Scheduled refresh unit guidance

Status: Read-only inspection guidance. Unit-file presence does not authorize
linking, starting, enabling, disabling, updating, removing, or manually
triggering a service or timer. Before scheduler or provider work, read the
[current operating envelope](../../docs/rebuild/CURRENT_OPERATING_ENVELOPE.md)
and [scheduling contract](../../docs/rebuild/SCHEDULING_AND_LOCKING.md).

Exactly four recurring timers are recorded as approved and active. Agents may
inspect their status read-only, but may not change or manually trigger them.

## Closed: market-close timer

Stage 12E remains closed. The project-local
`quant-data-market-close.service` and `quant-data-market-close.timer` files
are retained artifacts only. Do not link, start, enable, test, or supply a
credential to them. Stage 12C remains the sole reviewed write to
`data/market.sqlite`.

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
It sequentially refreshes the twelve established Treasury-curve, rates,
liquidity, financial-conditions, credit, Treasury-cash, gas-storage,
recession-chronology, and BLS price/wage/productivity operations. Its total
provider-request cap is 13 per invocation, it does not retry, and unchanged
content causes no canonical write. It is non-persistent, so missed windows are
not replayed automatically. The existing GDP/CPI, employment, and FMP-calendar
timers retain their separate cadences.

These inspection commands are read-only:

    systemctl --user status quant-data-macro-current-refresh.service
    systemctl --user status quant-data-macro-current-refresh.timer
    systemctl --user list-timers quant-data-macro-current-refresh.timer --all

Do not manually start the service or change the timer.
