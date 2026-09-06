# FMP research-input handoff — 2026-09-06

Status: completed. Canonical publication finished at `2026-09-06T18:32:28.751777Z`;
immutable reads, public-tool checks and exact replay verification passed.

This follows the [original coverage audit](RESEARCH_INPUT_COVERAGE_AUDIT_2026-09-06.md)
for the MSFT FY2025 research packet and AAPL comparison inputs. The user authorized
FMP repairs, investigation of the price gaps, a live quote tool, and this handoff.
Historical estimates/consensus reconstruction is explicitly deferred.

## What the repair supplies

The canonical company store now contains 385 source rows across 26 new snapshots:
145 statement/segment rows, 40 current estimate rows and 200 earnings rows.
Twenty-five responses were acquired in this batch; the existing AAPL annual
estimate response was reused with its original September 5 capture timestamp.
The new company tables preserve source JSON, content hashes, original row
pointers, fiscal labels, currency missingness and capture time.

| Input | MSFT | AAPL | Endpoint |
| --- | --- | --- | --- |
| Income statement | 3 annual, 12 quarterly | 2 annual, 12 quarterly | `income-statement` |
| Balance sheet | 3 annual, 12 quarterly | 2 annual, 12 quarterly | `balance-sheet-statement` |
| Cash flow | 3 annual, 12 quarterly | 2 annual, 12 quarterly | `cash-flow-statement` |
| Full as reported | 3 annual, 12 quarterly | 2 annual, 12 quarterly | `financial-statement-full-as-reported` |
| Product segments | 3 annual, 12 quarterly | 2 annual, 12 quarterly | `revenue-product-segmentation` |
| Current estimates | 10 annual, 10 quarterly target periods | 10 annual, 10 quarterly target periods | `analyst-estimates` |
| Earnings | 100 dated source rows | 100 dated source rows | `earnings` |

Segments are product categories as described by [FMP's endpoint documentation](https://site.financialmodelingprep.com/developer/docs/stable/revenue-product-segmentation).
The actual retained Microsoft segment payloads do not establish Microsoft Cloud
revenue or Azure growth. FMP ignored the segmentation request limit; all original
bytes were retained, but only the requested newest rows enter the reader.

Use `company.get_research_inputs@1.0.0` for these inputs. Existing
`company.get_fundamentals@2.1.0` remains the two-ratio tool. This work does not
populate the legacy guidance, historical revision or research-setup placeholders.

## How to consume the tools

See [LOCAL_AGENT_TOOLS.md](../LOCAL_AGENT_TOOLS.md) for the fixed local launcher,
manifest and response contract. Registry 2.71.0 / catalog 2.28.0 advertises
77 logical tools, including the two additions. The previous 2.70.0 registry
projects byte-exactly; existing tool versions and their frozen contracts remain
available.

Example for eight latest MSFT income quarters:

```bash
cd /home/volatility/Python_Projects/Quant_Data_Infra
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.tool_platform.local_agent_cli call <<'JSON'
{"api_version":"1.0","tool":"company.get_research_inputs","tool_version":"1.0.0","arguments":{"cik":"0000789019","endpoints":["income-statement"],"period":"quarter","limit":8}}
JSON
```

For MSFT FY2025/FY2024 comparisons, query annual with limit 3 and select the
returned fiscal labels/period ends. AAPL's CIK is `0000320193`. Request each
endpoint separately when complete endpoint coverage is needed: the global result
limit is at most 100 and mixed endpoints share that limit. Follow truncation
metadata; there is no pagination token for this bounded reader.

Each `result.records[].fields` array is a name/value mapping. Parse
`payload_json` as strict JSON, then select the source field explicitly.
`period_end`, `fiscal_year`, `fiscal_period`, `period`, `captured_at`,
`contenthash` and `source_row_pointer` accompany the payload.

Live quote example:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.tool_platform.local_agent_cli call <<'JSON'
{"api_version":"1.0","tool":"price_realtime","tool_version":"1.0.0","arguments":{"ticker":"MSFT"}}
JSON
```

Within this project's Python environment, the convenience function is
`from quant_data.tool_platform.realtime_quote import price_realtime`, followed by
`price_realtime("MSFT")`. Each invocation makes one host-controlled request to
[FMP's Stock Quote endpoint](https://site.financialmodelingprep.com/developer/docs/stable/quote).
It has a four-second timeout, no retry, no fallback and no SQLite write. Generic
server/Inspector hosts leave the capability disabled; the local agent launcher
explicitly enables it. Discovery performs no request or credential lookup.

Always inspect `quoted_at`, `captured_at`, `quote_age_seconds` and warnings.
The value is the provider's latest reported trade. Missing bid/ask, currency
and exchange-open status are not inferred.

The three live checks at approximately 2026-09-06 15:29 UTC succeeded:
MSFT 499.70, AAPL 319.97, SPY 770.19, each with a September 4 20:00 UTC provider
timestamp. Currency was unspecified by the source. Sunday responses correctly
flagged quotes older than 15 minutes. These are validation captures, not a
promise that those prices remain current.

## Price-gap diagnosis and repair scope

The canonical market store now includes all 34 repaired sessions:

| Symbol | Window | Missing sessions covered |
| --- | --- | ---: |
| AAPL | 2026-08-17–2026-08-28 | 10 |
| MSFT | 2026-08-17–2026-09-01 | 12 |
| SPY | 2026-08-17–2026-09-01 | 12 |

August 17–28 preceded activation of the daily collector. The earlier Stage12C
repair covered August 13–14, and the daily timer began normal execution on
August 31 with no historical catch-up.

On August 31 and September 1, collector v1.1 stopped on AVB's HTTP 200 empty
array at ordinal 55. AAPL at ordinal 1 had already published; MSFT at ordinal
379 and SPY at 497 were never reached. On September 2 v1.3 advanced farther
but stopped at a malformed index response. September 3–4 v1.4 processed the
entire 630-item universe: 619 published, two terminal symbols and nine isolated
provider failures. The requested three symbols published on those recent days.

The current collector already isolates the observed symbol-level failures.
No new defect in its current price path was established. Its aggregate
exit 69/store_unavailable label can represent a batch with isolated provider
failures and does not by itself establish a SQLite outage. Transport ambiguity
and publication/store failures retain their accepted stop behavior.

The repair uses the established price publisher, a fixed missing-only plan,
physical-store locking and atomic complete responses. It neither overwrites
existing sessions nor reconfigures the recurring collector. Ubuntu was restarted
twice under the user's restart authorization after its host control connection
failed. Normal clock-driven collection remained authorized; the hourly news
collector completed a scheduled run during validation.

## Benchmark improvements and remaining gaps

The retained data supports 18 of the packet's 27 benchmark facts with exact
numeric matches and an explicit source USD unit, compared with eight established
by the original warehouse audit. This is source-field comparison evidence,
not a completed rerun of the other project's packet evaluator.

There is a real provider-field discrepancy for MSFT FY2025:
`balance-sheet-statement.cashAndShortTermInvestments` is 94,555 million USD,
while the benchmark requires 94,565 million. The full as-reported
`cashcashequivalentsandshortterminvestments` contains the benchmark's numeric
value, but that payload does not state its currency. Preserve and disclose
the conflict; do not silently substitute the normalized field.

Four additional benchmark values are present numerically in full as-reported
data, with source currency unspecified:

| Benchmark measure | Numeric value in millions | Raw tag |
| --- | ---: | --- |
| Finance lease asset additions | 20,511 | `rightofuseassetobtainedinexchangeforfinanceleaseliability` |
| Finance lease principal cash payments | 2,283 | `financeleaseprincipalpayments` |
| Finance lease liabilities | 46,172 | `financeleaseliability` |
| Operating lease liabilities | 22,861 | `operatingleaseliability` |

These four values, and the cash alternative, are evidence requiring unit/context
qualification. They are not counted among the 18 fully matched facts.

Four requested benchmark facts remain absent from the acquired payloads:
Q4 Microsoft Cloud revenue 46,700 million, Q4 Cloud growth 27%, Q4 Azure growth
39%, and uncommenced leases 92,700 million. Original filing/earnings narrative
evidence is still needed for these.

Other material limits:

- Both transcript-date requests and both historical press-release requests
  returned HTTP 402 subscription restrictions. Transcript content and the
  requested press-release history were not acquired.
- Original filing sections/full text and management guidance are still missing
  from the requested public research-input surface.
- Full as-reported data has flattened XBRL context/unit information. A quarterly
  request does not establish every raw tag as a standalone quarter. Use the
  explicit standard statement period for ordinary quarter comparisons.
- Full as-reported top-level dates sometimes disagree by one day with the nested
  document period end. The reader uses the nested date and preserves both with
  a warning. Timezone-free `acceptedDate` remains source text.
- Cash PPE investment is a negative cash-flow source field. A positive cash-addition
  measure requires an explicit sign transformation. Lease additions, lease cash
  payments, liabilities and uncommenced commitments are distinct. Standard
  `totalDebt` must not be assumed to exclude leases.
- Current estimate snapshots are not historical consensus vintages. Currency
  and accounting basis remain unspecified where FMP omits them; page-limit
  warnings remain explicit. Historical reconstruction is deferred as requested.
- The repaired inputs became available locally in September 2026. A July 2025
  as-of cutoff must not see them. Reconstructed historical analysis remains
  qualified accordingly; do not upgrade the prior packet's point-in-time status.
- The daily price variant retains its existing provider-native adjustment
  semantics. This repair does not establish total-return or dividend-adjusted
  prices.

## Execution and validation evidence

Company migration `company:0008_fmp_research_inputs` was applied at
`2026-09-06T18:28:16.997079Z`. Publication completed at
`2026-09-06T18:32:28.751777Z` with 26 company snapshots / 385 source rows and
34 new price versions.

The full offline suite passed across resumed executions: **1,607 unique cases**,
with no unresolved failures, errors, skips or uncovered cases. Four duplicate
import discoveries and one duplicate Stage9 CLI execution were not counted as
additional cases. Earlier failed and interrupted runs remain in the evidence.
One Stage6 teardown guard observed an authorized hourly news-store update;
the unchanged full test, including its guard, passed on a separate rerun.
Independent review accepted the implementation and exact coverage accounting.

Post-publication immutable checks verified all 385 company rows against retained
source hashes and row identities, the new tables' integrity and foreign keys,
and all 34 repaired price versions against their raw OHLCV responses.
The public company tool returned the expected counts in all 26 endpoint/period
queries. July 2025 as-of queries correctly returned no new captured evidence.
The public price tool returned all four OHLC series, each with 15 complete
sessions from August 17 through September 4, for MSFT, AAPL and SPY.

All 29 exact retained-response replays returned unchanged with zero rows/versions
written and no provider requests. Company and market main-file SHA-256, byte
counts and modification timestamps matched before and after replay.
These fingerprints do not claim sidecar-file byte equality.

A publication-helper assertion initially expected only the newly applied
migration from an API that returns the complete ledger. The existing migration
and empty tables were verified before continuing only the pending publication;
the migration was not repeated. The public-price verification helper also
needed its required cutoff/date-policy arguments. Both helper corrections and
the original failure evidence are retained; neither changed production code.

The finite provider ledger contains 35 attempts against a cap of 40:
25 successful company input responses, four HTTP 402 availability checks,
three successful price responses and three successful quote calls. No retries
were attempted. One existing AAPL estimate response was reused without network.

Private evidence is under `.local/fmp-research-repair-20260906/`:
`publication-inputs.txt`, `company-preflight.txt`,
`benchmark-after-fmp.txt`, `identity-evidence.txt`,
`quote-smoke-{MSFT,AAPL,SPY}.txt`, `full-suite-reconciliation.txt`,
`independent-review.txt`, `migration-publication.txt`,
`company-publication-receipts.txt`, `price-publication-receipts.txt`,
`post-publication-checks.txt`, and `canonical-replay-verification.txt`.
Original failed/interrupted logs and their successful rechecks are retained.
Retained source bytes live in the established private operations blob store.
The dated audit remains unchanged as evidence of the original state.
