# Stage 10 additive IWM ETF evidence

## Status

The additive IWM price extension completed on 2026-08-27. It does not amend
the sealed 629-instrument Stage 10 cohort or its historical receipts. The
reviewed 95-member `curated_etfs` snapshot remains intact; one separate
successor contains exactly those 95 identities followed by IWM.

The one authorized broad Alpaca ETF option-grid attempt also completed, but
failed closed before publication. It issued 17 bounded requests, returned an
`incomplete_universe` receipt for all 15 underlyings, and wrote zero option
captures, contracts, surfaces, or canonical rows. It was not retried.

## Implemented boundary

- Registry revision: `2.50.0`
- Registry SHA-256:
  `0adc78cbe419b18ece989c9cdd6d918ef13113fa5f573d6f5fbe045b9eca8259`
- IWM collector: `fmp.market.iwm_etf_daily_history`
- Existing datasets reused: Stage 10 evidence, instruments, universes, and
  versioned daily prices
- Scope SHA-256:
  `6721ad6b8607a8aa6aca91f467b792ee76f91e2957ed06ae5402b05a99091e93`
- Frozen-base scope SHA-256:
  `0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd`
- Successor membership SHA-256:
  `c0d84f5ff2c35dc13d44da136b04a84687dad7242527b86abcb555f99c7f67b1`
- Stable IWM instrument ID:
  `stage10_instrument_ccbc7472db979301ebd5c839223783f6`
- Stable IWM identity-seed SHA-256:
  `d057469aa19c7a71b9a4d15c5debd217d28059e54ac0dff72bfa1dd8429724df`

The operation is fixed to one FMP request, one attempt, 30,000 rows, 16 MiB,
and a 45-second per-request timeout. It resolves the existing `FMP_API_KEY`,
performs provider work before the physical market-store lock, and publishes
atomically through the existing ingestion coordinator. Before provider work,
it durably reserves `data/.operations/fmp-iwm-etf-history-v1/` at mode `0700`.
Only a successful publication creates its `completion.json` at mode `0600`
with `O_EXCL`; any reserved or completed state blocks another invocation.

No migration, dataset, registry job, timer, public tool, dashboard, export,
hosting, deployment, promotion, retirement, or destructive action was added.

## Offline validation

The dependency-free repository suite passed after implementation:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
Ran 1184 tests in 3114.621s
OK
```

Independent verification additionally passed 10 focused IWM tests and 36
adjacent Inspector, options, generated-contract, and registry-projection tests.
It proved exact `95 -> 96` membership, no-write semantic replay, append-only
correction behavior, atomic rollback under an injected writer failure, exact
registry `2.50.0 -> 2.49.0 -> 2.48.0` projection, no IWM job binding, and no
migration or systemd diff. `git diff --check` also passed.

One bounded limitation remains: 45 seconds is the socket/request timeout, not
an end-to-end monotonic deadline for parsing and publication.

## Immutable preflight

The approved descriptor-held `mode=ro&immutable=1` reader accepted a quiet
`data/market.sqlite`. Before credential or provider access it established:

- `PRAGMA integrity_check`: `ok`
- `PRAGMA foreign_key_check`: zero rows
- exact 95-member base snapshot and membership hash
- zero IWM instruments, captures, versions, and current rows
- zero successor scopes, snapshots, and members
- no IWM operation-state directory
- frozen Stage 12C state unchanged at 619 captures, 1,238 captured rows,
  1,238 versions, and 1,238 current rows
- total pre-write Stage 10 counts of 629 instruments, 1 scope snapshot,
  5 universe snapshots, 745 universe members, 5,210 price captures,
  4,237,873 price versions, and 4,237,131 current prices

## One live IWM result

The zero-argument operation
`python3 -m quant_data.operations.fmp_iwm_etf_history` was invoked once. It
returned `outcome=succeeded` with:

- 1,254 price rows, spanning `2021-08-30` through `2026-08-27`
- 288,056 admitted response bytes and HTTP 200
- response SHA-256:
  `dfc44adb7cadbf650eedf6a615442cbe045be2b714a41540aa1b629432a65e69`
- normalized-history SHA-256:
  `6338a0330718a22e17f094c956205986cc4a715dd8e7b2ca3abf92d11efc82c6`
- 96 successor members
- 1,354 reported writes

The durable completion receipt repeats those values and has one link at mode
`0600`; its parent attempt reservation is a real directory at mode `0700`.

## Immutable postflight and Inspector result

The full post-write immutable integrity scan completed successfully and the
foreign-key scan returned zero rows. A separate compact projection corrected
only a reporting-script BLOB-serialization issue; it did not repeat the
integrity scan or provider request.

Postflight established:

- exactly one FMP/provider-native IWM identity with the reviewed stable ID and
  seed hash
- exactly one successor scope and one complete 96-member successor snapshot
- exact ordered membership, with SPY first and IWM last
- exactly one complete IWM capture with the receipt response hash
- 1,254 distinct source rows, source rows `1..1254`, and correction sequence
  exactly 1
- 1,254 current IWM rows and zero current/version identity mismatches
- unchanged 95-member base and unchanged frozen Stage 12C counts
- post-write totals of 630 instruments, 2 scope snapshots, 6 universe
  snapshots, 841 universe members, 5,211 price captures, 4,239,127 price
  versions, and 4,238,385 current prices

The fixed Canonical Inspector's own immutable read returned HTTP 200 and
`execution=read_only` for `market-prices&symbol=IWM`: 1,254 UI-visible rows.
The latest row is `2026-08-27`, close `299.81`, volume `13,510,462`.

## One broad Alpaca option-grid result

Only after the accepted IWM postflight, the zero-argument operation
`python3 -m quant_data.operations.alpaca_etf_options_refresh` was invoked
once. Its fixed bounds were 15 ETFs, DTE targets
`1,2,3,7,14,30,60,90,180,365`, 362 requests, 900,016 rows, 256 MiB, and
900 seconds, with no retry.

The result was a sanitized `incomplete_universe` receipt:

- session date: `2026-08-27`
- requests issued: 17
- completed underlyings: none
- failed underlyings: SPY, QQQ, IWM, DIA, XLB, XLC, XLE, XLF, XLI, XLK,
  XLP, XLRE, XLU, XLV, XLY
- captures, contracts, surface rows, and writes: all zero

The request pattern is one OPRA calendar call, one shared underlying-snapshot
call, and one contract-catalog call per ETF; no option-chain call was reached.
The catalog endpoint, host, filters, and pagination names still match Alpaca's
current official API reference. The operation intentionally emits no provider
body or per-symbol exception text, so this receipt cannot distinguish an empty
or account-limited catalog from a live contract-shape/eligibility rejection.
No speculative parser change is accepted from that ambiguity.

The Inspector subsequently returned zero IWM option rows and 292 pre-existing
SPY option rows. Therefore IWM price accumulation is live and UI-visible, but
the broad option grid did not begin accumulating data. A new provider attempt,
diagnostic request, retry, or scheduler change requires a new explicit user
decision; none was performed here.
