# ETF price-gap repair — September 15, 2026

Status: completed and verified.

The user requested fixing the gaps confirmed in the immediately preceding
25-ETF audit. This finite repair filled all 304 missing ETF/session
observations in the canonical market store. The September 13 investigation
and September 15 pre-repair audit remain dated evidence of the earlier state.

## Result

Verified through public tools at 2026-09-15T19:45:45.179372Z:

| Scope | Result |
| --- | --- |
| SPY, IWM and the other 23 allocator ETFs | All 25 have 9/9 August features |
| August feature endpoint | August 31, 2026, with 272/272 expected lookback sessions per ETF |
| Available feature values | 225/225; previously 33/225 |
| Latest retained daily price | September 14 for all 25 |
| August 1–September 14 coverage | 30/30 expected sessions per ETF; zero gaps |

September 15 had not closed at the verification time and was excluded.

## Finite operation

- 23 ETFs: August 17–September 1, 12 missing sessions each.
- IWM: August 28–September 1, three missing sessions.
- All 25 ETFs: September 14, one missing session each.
- Exactly 49 single-attempt FMP full-EOD requests succeeded, retaining
  69,274 response bytes. There were no provider retries.
- The operation used the existing FMP_API_KEY resolver, shared FMP allowance,
  active price selection, parser, physical-store locks and replay-safe
  publisher. Each response was further limited to 64 KiB and the run to
  900 seconds.
- All responses were retained before publication. Exact expected dates,
  symbols and the existing price parser were validated before the first
  canonical write.
- The publisher added 49 captures and 304 price versions/current rows to
  data/market.sqlite. The work changed no production code, binding, registry,
  migration, provider, recurring unit or schedule.
- This completed finite population must not be repeated without a new
  explicit repeat scope.

## Validation

All 30 focused existing price-window, price-publisher, acquisition-only queue
and FMP allowance tests passed. An additional explicit temporary-store check
published 13 sample rows, rejected six incomplete/duplicate/wrong-symbol
cases, and replayed both sample responses with zero writes.

The final immutable audit compared all 304 canonical OHLCV rows, source-row
pointers and original capture bytes with their retained responses. It checked
current-version pointers, original capture times and source artifact/snapshot/run
references. All 143,771 prior current prices, 143,848 prior versions, 345 prior
capture metadata records, 25 identities and the migration ledger were preserved.

All 49 exact response replays returned unchanged with zero canonical writes
and zero provider requests; complete scoped data digests also remained unchanged.
The public ETF snapshot and all 25 bounded price reads succeeded without
truncation. Repeating the original pre-repair cutoff returned exactly the
original 25 records with 33 available feature values, proving the new captures were not backdated.

The full offline suite was not run for this bounded operation using existing
production interfaces. A local deadline-type error was corrected before any
request. An initial unindexed read-only verification was stopped and replaced
with equivalent indexed checks; only the completed checks above count as
validation. The original local failures and corrections are retained.

## Evidence and remaining limitations

[Private evidence](../../.local/etf-gap-repair-20260915/) contains manifest.json,
plan.json, before-audit.json, acquisition.json, publication.json,
verification.json, public-verification.json, cutoff-verification.json, complete
public requests/responses, focused-tests.log and fixture-validation.json.
Original request/response/publication receipts and bytes are under
data/.operations/etf-gap-repair-20260915/.

The pre-repair audit is preserved at .local/etf-gap-check-20260915/.
The [September 13 investigation](ETF_ALLOCATOR_INVESTIGATION_2026-09-13.md)
explains the earlier gaps and feature convention.

Mixed provider capture vintages and unestablished historical publication or
adjustment reconstruction remain explicit tool warnings. Numerical completeness
does not establish those properties. No common-vintage full-history refresh,
classification enrichment or scheduler change was performed.
