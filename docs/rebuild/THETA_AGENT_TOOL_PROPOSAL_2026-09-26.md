# Theta options tools for other local agents

Status at proposal date: proposed tool contracts. Superseded for current use by
the implemented [September 27 contracts](THETA_AGENT_TOOLS_2026-09-27.md), following
the user's explicit implementation approval. The original proposal below is
preserved as dated context.
Decision/source boundary: the user requested tool recommendations and specified
that new options analysis must use the new options.sqlite source, with Alpaca
options collection retired. The scheduler retirement is complete; proposed
tool implementation is a separate next step.

## Source boundary

New contracts must read only the host-selected `data/options.sqlite` through
`OptionsStore`, using the established physical lock and immutable procedure.
Their provider is `thetadata`, their store role is `options`, and their source
datasets are the existing optional-registry Theta datasets. They must never
substitute Alpaca records from market.sqlite or intraday observations from
monitor.sqlite. Missing source data remains unavailable, without provider calls.

Runtime inspection on September 26 found registry 2.92.0. Explicit
`options.search_captures@2.0.0`, `options.search_contracts@2.0.0` and
`options.get_surface_snapshot@2.0.0` still read the retained Alpaca
paper/indicative cohort in market.sqlite. They are legacy archive interfaces,
not Theta readers. The existing 1.0.0 options tools are older fixture contracts.
Their payloads must not be silently relabeled or switched to a different source.
The current manifest therefore does not yet offer the Theta tools proposed below.

Prefer new descriptive names with explicit 1.0.0 contracts, preserving older
version semantics. Existing response lineage enums assume the original four
store roles; a Theta adapter needs an honest versioned options-domain lineage
contract. Do not mislabel the options store as market simply to pass that schema.

## Recommended order

| Priority | Proposed tool | Purpose and output |
| --- | --- | --- |
| 1 | `options.get_coverage@1.0.0` | Available ETF roots, first/last saved session, gaps within a bounded requested interval, capture freshness, Greek/OI coverage and collection status. Start every analysis here. |
| 1 | `options.get_daily_history@1.0.0` | Saved or derived daily call/put volume, open interest with its effective date, put/call ratios, concentration and supported ATM IV proxies; bounded symbol/date selection, typed series and source lineage. |
| 1 | `options.get_daily_snapshot@1.0.0` | One coherent capture per ETF/session: full-chain summary cells by DTE and moneyness, call/put sides, quote-quality statistics, retained activity leaders and selection coverage. |
| 2 | `options.get_volatility_profile@1.0.0` | Supported 7/30/90-day ATM IV proxies, term slopes, explicitly defined moneyness-based skew and trailing IV ranks/percentiles. Report anchor distance, interpolation inputs and missing terms. |
| 2 | `options.screen_activity@1.0.0` | Rank the 15 ETFs by daily volume relative to their own trailing history, put/call changes, 0DTE activity share, concentration and IV changes, with sample counts and quality gates. |
| 2 | `options.get_selected_contracts@1.0.0` | Drill into the at-most-300 retained details by symbol/session, expiry, right and strike; quote fields, retained Greeks, volume/OI, selection reasons and quality. This is selected-contract search, not a complete-chain listing. |

A useful first implementation is the three priority-1 tools. The analytical
tools can then consume the same primitives and versioned outputs instead of
building parallel calculations. Reuse the existing daily-chart projection for
supported IV/volume/OI fields, retaining its exact derivation version and flags.
There is no need for a new provider subscription or refetch to build these reads.

## Rules for useful, honest outputs

- Scope is the 15 stored ETFs. Do not imply historical daily coverage for the
  2,251 single names present only in the separate intraday monitor universe.
- Keep full/selected/remainder summary populations distinct. The retained
  top-five leader records are bounded references; they do not restore the
  discarded raw chain.
- Every response identifies provider, dataset/store role, session, capture ID,
  actual capture time, source semantic hash, derivation version, quality,
  missingness, and bounded pagination/truncation state.
- IV uses retained selected anchors and the existing root-proxy assumptions.
  Constant maturity uses bracketed total-variance interpolation; unavailable
  brackets remain null. A moneyness skew must not be labeled 25-delta skew.
- Volume/OI ratios and activity describe observed activity, not buyer/seller
  direction, opening/closing positions or dealer inventory.
- Existing historical data was acquired in a later backfill. A capture-time
  cutoff is local availability, not proof of original historical public
  availability. Keep historical point-in-time status unestablished.
- Rolling statistics use trailing dates with explicit lookback, minimum
  observations and missing-session rules. Report comparisons' common session
  and freshness rather than silently ranking unequal or stale dates.
- Cross-source analytics such as implied-minus-realized volatility can later
  compose explicitly with the versioned market-price tools. Keep price
  provenance separate; the options input still comes solely from options.sqlite.
- Do not advertise exact full-chain gamma/delta exposure, dealer positioning,
  max pain, real-time executable pricing or strategy backtest validity from
  the compact retained data. Those require additional inputs or narrower,
  explicitly qualified models.

## Access and validation boundary

Expose the new contracts through the existing local `bin/quant-data-tools`
manifest / describe / call interface. Other agents run its absolute WSL path,
select the advertised explicit tool version, and preserve the complete result
and receipt. No direct SQL, caller-selected file path, write connection,
provider call, network server or hosting is needed.

Implementation should use isolated temporary-store tests proving source
identity, no fallback to market.sqlite, no writes, bounds, consistent capture
selection, missingness, capture-time cutoffs and compatibility with the existing
daily reader. Registry/contract changes need their focused predecessor and
manifest checks and independent review if their affected boundary requires it.
No new tool implementation or executable tool tests are claimed by this proposal.

## Alpaca options retirement evidence

At 2026-09-27T03:42:50Z (September 26 Eastern), the user-requested retirement
was complete: `quant-data-alpaca-spy-options.timer` had no installed timer
definition, was inactive and had no next activation. The collection service
was idle before the change and remains inactive. `systemctl disable --now`
removed the timer's linked definition along with enablement; its transient
failed/not-found state was cleared without starting any service.

The repository unit definitions and historical database evidence remain.
The Theta daily/weekly timers and separate monitor timer kept their enabled,
active/waiting states and next trigger times. No provider request, canonical
store access, data deletion or unrelated Alpaca/news change occurred.

Evidence: `.local/options-tool-review-20260926/manifest.json`, the three
versioned describe responses, and `alpaca-retirement.json`.
