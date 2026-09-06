# FMP research repair and live quote — 2026-09-06

The owner authorized MSFT/AAPL research input repair using FMP, investigation
and repair of their daily price gaps with SPY, a `price_realtime(ticker)`
tool, local agent documentation and a handoff. Historical estimate/consensus
reconstruction is deferred. The owner subsequently authorized restarting
Ubuntu WSL to recover a failed host connection and finish this work.

## Finite acquisition and publication

The entire batch is capped at 40 provider requests, one attempt per exact
request, no automatic retry. The private attempt ledger is
`.local/fmp-research-repair-20260906/`. Existing host credentials are reused.
No recurring unit is manually triggered or reconfigured by this repair.

Company requests cover MSFT and AAPL: income, balance sheet, cash flow,
full as-reported statements, product segments, current analyst estimates,
earnings, and bounded transcript/press-release availability. Annual limits
are three for MSFT (including FY2024) and two for AAPL; quarterly limits are
12. Segments may ignore the request limit: retain the raw response, but
normalize only the requested most recent rows. Estimates are one page of
10 target periods; earnings are bounded to 100 rows per symbol. Quote
validation is one call each for MSFT, AAPL and SPY.

| Price repair | Window | Required sessions |
| --- | --- | --- |
| AAPL | 2026-08-17 through 2026-08-28 | 10 |
| MSFT | 2026-08-17 through 2026-09-01 | 12 |
| SPY | 2026-08-17 through 2026-09-01 | 12 |

The exact sessions are August 17–21, August 24–28, and, for MSFT/SPY,
August 31 and September 1. These are three single-attempt requests to
`/stable/historical-price-eod/full`. Publication requires each complete
exact date set and rejects any existing current observation, except a
completed exact semantic replay that causes zero persistent writes.
Raw bytes are retained before publication. The established physical-store
lock, descriptor checks, price schema and replay-safe transaction remain
in use. This authority does not repeat Stage12C or invoke Stage12E.

## Company data semantics

Company migration `0008_fmp_research_inputs` adds immutable evidence
references in `company_fmp_research_snapshots` and source-row envelopes in
`company_fmp_research_rows`. Dataset ownership is
`company.fmp.research_evidence` and `company.fmp.research_inputs`;
the registered producer is `fmp.company.research_inputs`.

`company.get_research_inputs@1.0.0` uses the quiet immutable company reader.
It accepts CIK, endpoint filters, annual/quarter/all, latest/as_of, an
optional offset-aware cutoff, and a result limit no greater than 100.
Callers cannot supply paths, SQL, credentials or network controls.
Availability is the retained local capture; neither a historical reporting
date nor a timezone-free acceptedDate establishes historical availability.

Annual FY and standalone Q4 remain distinct with the same end date.
The source payload, fiscal labels, reported currency, digest and original
row pointer are retained. Full as-reported rows use the nested document
period end when the top-level provider date disagrees; both are retained
with a warning. Missing currency and estimate basis remain explicit.

Cash-flow PPE investment, lease asset additions, debt carrying values,
lease liabilities and future commitments are distinct measures. Product
segment categories do not establish Microsoft Cloud/Azure narrative growth.
Existing SEC versions and old tool semantics remain unchanged.
`company.get_fundamentals@2.1.0` retains its two-ratio contract; the new tool
supplies source statement and earnings inputs.

## Live quote boundary

`price_realtime@1.0.0` accepts only a ticker. Each call makes at most one
FMP `/stable/quote` request with fixed timeout and response bounds, no
redirects, retries, fallback or store access. Invalid inputs fail before
credential resolution. The local launcher explicitly enables
`fmp_quote_live`; generic server/Inspector hosts leave it disabled.
Construction and discovery perform no provider request.

The result is FMP's latest reported trade, with price, quote timestamp,
fetch timestamp, age and available volume/day fields. Bid/ask are nullable;
currency is not inferred. Quotes older than 15 minutes are flagged, also
when the exchange is closed. Last trade does not imply an executable quote.

## Required evidence

Focused input, cutoff, immutable-read, replay, finite-repair and capability
tests, the full offline suite and a fresh independent review precede
canonical publication. Final counts, actual execution receipts and remaining
gaps belong in the companion handoff. This contract is not activation proof.
