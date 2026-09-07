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

## Active current-news refresh timer

On 2026-08-30 the reviewed `quant-data-current-news-refresh.service` and
`.timer` were linked into the per-user manager, daemon-reloaded, enabled, and
started. The timer is active/waiting at the hourly `:10` UTC cadence with
`Persistent=false`; activation left `LastTrigger` empty and the service
inactive/dead with no execution timestamps, provider request, or store write.

The fixed zero-argument service runs ten source steps after the authorized
September 6, 2026 extension: the original FMP, official RSS, and Alpaca/Benzinga
batch plus Finviz and FinancialJuice. Each website adds one credential-free
request; the generic ceiling is 24 requests plus one FMP stock-latest request.
The batch has no retry or catch-up and uses the current bounded market-database
universe. Missing credentials remain source-local unavailable outcomes. The
extension changed the installed module, not the existing units or cadence;
dated activation evidence is in the operating envelope. These commands are
read-only:

    systemctl --user status quant-data-current-news-refresh.service
    systemctl --user status quant-data-current-news-refresh.timer
    systemctl --user list-timers quant-data-current-news-refresh.timer --all

Do not manually start the service or retry, broaden, reinstall, disable,
update, remove, or repurpose the timer.

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
outcomes are terminal, and other missing or error outcomes fail closed. A
non-sentinel unapproved empty response or malformed or out-of-contract HTTP
200 payload is recorded as a per-symbol failure without publication; later
independent symbols continue, but the aggregate writes no completion receipt
and exits nonzero. Durably received redirect and status/media-policy failures
are also isolated per symbol. Transport ambiguity without a durable response
and publication/store failures remain fail-fast.

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
It sequentially refreshes 29 established operations covering Treasury
curve and auctions; rates, liquidity, and NY Fed Primary Dealer Statistics;
industrial production, financial conditions, national activity, and credit;
both CFTC futures-only positioning families; Treasury cash, debt, and fiscal
balance; gas storage, weekly petroleum stocks and product supplied,
electricity retail, recession chronology, BLS price/wage/productivity, and BEA
personal income/outlays. ECI rides the existing BLS request. The total
provider-request cap is 94 per invocation, it does not retry, and unchanged
content causes no canonical write. H.8/SLOOS entered the wrapper after the September 5 repair accepted all
13 fixed responses. Their window starts at the previous quarter's start;
CFNAI now uses FRED from 2026-01-01. It is non-persistent, so missed windows are
not replayed automatically. The existing GDP/CPI, employment, and FMP-calendar
timers retain their separate cadences.

These inspection commands are read-only:

    systemctl --user status quant-data-macro-current-refresh.service
    systemctl --user status quant-data-macro-current-refresh.timer
    systemctl --user list-timers quant-data-macro-current-refresh.timer --all

Do not manually start the service or change the timer.

## Active in-place Alpaca ETF option-surface timer

The enabled `quant-data-alpaca-spy-options.timer` retains its legacy name and
`Persistent=false`. On 2026-08-30 the user explicitly authorized updating its
service in place to invoke `quant_data.operations.alpaca_etf_options_refresh`
instead of the SPY-only wrapper at the then-current 15:55 America/New_York
weekday cadence. On 2026-08-31 the user authorized moving the same fixed timer
to 16:20 America/New_York so collection begins after the latest ordinary
ETF-option session. The focused unit test and `systemd-analyze --user verify`
passed. The daemon reload made no provider request or store write, but
restarting the active timer unexpectedly dispatched the already-passed
same-day event at 20:29 EDT despite `Persistent=false`. That unplanned
incomplete-universe run issued 64 requests, retained 54 captures, materialized
8,812 surface rows, and made 27,031 writes. It completed SPY, QQQ, IWM, DIA,
XLB, and XLC, while XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, and XLY failed.
It exited 75; it was not retried and its immutable captures were not deleted.
After completion the failure flag was cleared without starting the service.
The service is `inactive/dead` and retains its 20:29:50-20:30:17 EDT
execution timestamps and exit status 75. The timer is
`loaded`/`enabled`/`active`/`waiting` for Tuesday 2026-09-01 16:20 EDT.
No further service start was made.

The fixed universe is `SPY`, `QQQ`, `IWM`, `DIA`, `XLB`, `XLC`,
`XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLRE`, `XLU`, `XLV`, and
`XLY`, with target DTEs `1`, `2`, `3`, `7`, `14`, `30`, `60`,
`90`, `180`, and `365`. It uses the existing Alpaca credentials, paper
account, indicative feed, OPRA gate, and fixed `data/market.sqlite` target.

One invocation has no retry or catch-up and is capped at 362 single-attempt
requests, 900,016 rows, 256 MiB, and 900 seconds; the service timeout is 16
minutes. Exact response bytes are retained before normalization, partial grids
exit nonzero, and exact semantic replay writes nothing. The 18:00 Stage 12E
FMP market-close timer remains separate.

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

The generic SEC publisher is version `1.2.0`. It accepts only the exact
same-issuer transition from an immutable CompanyFacts fallback placeholder to
later submissions membership; it does not rewrite the filing row. All other
filing metadata conflicts remain fail-closed, and the service still has no
retry within an invocation.

## Active company market-data schedule

On 2026-09-05 the user explicitly approved creating and enabling the separate
`quant-data-company-market-refresh.timer` host exception. It runs on weekdays
at 19:00 America/New_York and invokes the zero-argument company refresh wrapper
for dividends, splits, and annual analyst estimates. The bounds are 700 retained
equities, 2,101 requests, 128 MiB, and 30 minutes per invocation, with no retry.
The service has a 31-minute timeout and `Restart=no`; the timer has
`Persistent=false`, no randomized delay, and no catch-up.

Both units are linked to these project files. Activation confirmed the timer
enabled and active/waiting, with its first trigger Monday 2026-09-07 at
19:00 EDT. The service remained inactive/dead without execution timestamps.
Activation did not perform a fetch or change the inspected stores/company state.
Read-only inspection:

```bash
systemctl --user status quant-data-company-market-refresh.timer
systemctl --user show quant-data-company-market-refresh.timer --property=NextElapseUSecRealtime,LastTriggerUSec,ActiveState,SubState,UnitFileState
systemctl --user show quant-data-company-market-refresh.service --property=ExecMainStartTimestamp,ExecMainExitTimestamp,ActiveState,SubState
```

Do not manually start or repurpose the service. Registry collectors and the
recovered `expectations` and `company-weekly` jobs retain their existing states.
See the [repair and activation receipt](../../docs/rebuild/FETCH_REPAIRS_2026-09-05.md)
for scope, coverage limitations, and private evidence.

## Equibles finite transcript backfill

The dedicated `quant-data-equibles-transcripts.service` and `.timer` implement
the user-authorized 519-ticker raw transcript backfill at 00:10 UTC daily.
Persistent catch-up still observes the durable shared daily 100-request cap.
The zero-argument service preserves each ticker's full available history before
advancing, stages partial pages privately, and publishes complete raw calls to
the company store. Completed populations make zero further provider requests.
See the [contract](../../docs/rebuild/EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md)
and operating envelope for the exact scope and dated activation evidence.

Read-only status:
    systemctl --user status quant-data-equibles-transcripts.timer
    systemctl --user status quant-data-equibles-transcripts.service
