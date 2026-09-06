# Research input coverage audit — 2026-09-06

This is the owner-requested coverage assessment of the sibling research agent's proposal. It does not implement that proposal or authorize collection. The warehouse has substantial retained company and market data, but cannot yet supply the complete research packet through supported latest contracts.

## Scope and executable evidence

- MSFT / CIK 0000789019; AAPL / CIK 0000320193 as validation; SPY as market benchmark only.
- Historical decision: 2025-07-31T12:00:00Z. Present decision: 2026-09-06T04:34:55.525523Z.
- Read the four named sibling requirement/packet/schema/report files. No sibling file was modified.
- Runtime manifest: registry 2.70.0, 75 logical tools. Described the latest versions of 14 relevant tools.
- Ran 68 public data calls, including all filing pages. Sixty-two returned results; six historical market OHLC/volume calls returned explicit unavailable-at-cutoff errors. Empty and not_established responses are not counted as populated coverage.
- Inspected normalized facts and bounded company source metadata using established readers and the descriptor-pinned quiet immutable gateway. No credentials, provider API data calls, store writes, scheduler actions, service restarts, or code changes.
- Full request/response receipts, descriptions, field comparisons, source hashes, capture times, store stamps, and workspace fingerprints are in ../../.local/research-input-coverage-2026-09-06/. See evidence_index.json and audit_provenance.json there.
- The existing workspace was dirty on branch codex/stage12-macro-2-21, HEAD 505374db88f2f77a8368a80643e49404137c6035. This audit preserves that work. Concurrent Inspector edits were observed in four files; the whole workspace was not frozen. The handoff source fingerprint is 559031891cbc47eac69c3a987cdd5ab8bfe4935becc97723fab47847f32eca51; per-file hashes and modified-since-start paths are recorded in audit_provenance.json. This is an assessment, not implementation acceptance or formal independent verification.

## Actual coverage

Counts below distinguish currently selected observations from stored versions and filing metadata from document text.

| Required input | MSFT retained evidence | AAPL retained evidence | Assessment |
| --- | --- | --- | --- |
| Issuer identity | One present issuer; historical issuer result not_established | Same | Present identity usable; historical local availability absent |
| Filing metadata, fully paginated | 1,054 present / 857 at historical source-date cutoff; present filing dates through 2026-09-02 | 1,031 present / 942 historical; through 2026-09-03 | Metadata exists; no text supplied |
| Reviewed normalized fundamentals, internal reader | 678 selected observations / 1,520 stored versions | 682 selected / 1,552 versions | Retained but latest public source-fact interface missing |
| Core financial period coverage | Through 2026-06-30; latest annual FY2026 and FY2025 | Through 2026-06-27; latest annual FY2025 and FY2024 | Does not establish eight standalone quarters |
| Share history, public v2 | 214 present / 202 historical rows | 217 present / 202 historical rows | Outstanding, weighted-average basic and diluted remain separate |
| Public fundamental ratios, since 2023-07-01 | 24 present / 16 historical | 26 present / 16 historical | Only net_margin and liabilities_to_assets |
| Corporate actions | Zero rows | 97 rows (92 dividends and 5 splits) | Partial issuer coverage; historical cutoff returns zero for both |
| Analyst estimates | Zero rows | 200 metric/statistic rows from ten annual-period source rows, one capture | AAPL is partial, not revision history |
| Earnings events | Zero | Zero | Empty |
| Management guidance | Zero | Zero | Empty |
| Estimate revisions and earnings setup | not_established | not_established | Explicit implementation placeholders |
| Filing sections, releases and transcript text | No public text reader in manifest | Same | Unsupported public access; full retained text coverage not established |

The ten reviewed mappings are revenue, net income, total assets, total liabilities, operating cash flow, outstanding shares, weighted-average basic/diluted shares, and basic/diluted EPS. There are no normalized operating-income, cash-PPE, cash/debt, lease or segment/cloud mappings in this retained set.

CompanyFacts source snapshots were first retained on 2026-08-25 at 20:37:33.690800Z for MSFT and 15:10:24.184563Z for AAPL. Their SHA-256 values are respectively f8aae2965b20ad0df44bdf7ccbedf797d275b6b8dc030154a7a311361bb7246f and 73a86c6aedc31f77cac2ea4df5f80f0b3bd7e6eb58bb4e01444fbedf3afb9c43. Submissions metadata has later retained captures on September 4. These dates describe retained artifacts, not provider freshness guarantees.

### Exact MSFT FY2025 benchmark

Eight of the packet's 27 reported figures match retained normalized annual facts, after exact Decimal scaling from USD millions to USD. They are FY2024 and FY2025 revenue, net income, diluted EPS, and operating cash flow. The historical source-date selection identifies the July 30, 2025 filing; the present selection can identify a later comparative filing.

| Packet facts | Count | Retained normalized coverage |
| --- | ---: | --- |
| FY2024/FY2025 revenue, net income, diluted EPS, operating cash flow | 8 | Exact value/period matches, internal reader |
| Q4 FY2024/FY2025 revenue and diluted EPS | 4 | No matching standalone Q4 contexts retained |
| FY2024/FY2025 operating income and cash PPE additions | 4 | Unmapped |
| Cash plus short investments, current long-term debt, noncurrent debt | 3 | Unmapped |
| Microsoft Cloud revenue/growth and Azure growth | 3 | Unmapped |
| Lease asset additions, principal payments, finance/operating liabilities, uncommenced leases | 5 | Unmapped |

The complete 27-field mapping is in benchmark_field_coverage.json. These eight available facts still lack a supported latest public fact contract; ratio operands provide only part of that material.

The current normalization deliberately selects one duration context per accession/concept/unit/end date. The reader then selects one value per metric/end date. MSFT's latest eight revenue/OCF period ends contain six standalone quarters and two full-year values; AAPL OCF includes cumulative YTD values. An annual or YTD value cannot be called a quarter. Supporting quarter and annual context coexistence needs a reviewed identity/schema design; deriving a missing quarter needs explicitly compatible annual/YTD operands and a named calculation.

### Temporal interpretation

Historical company filing/fact calls filter source availability. They can return a historical filing captured locally in August 2026. This supports labeled reconstruction, not proof of a contemporaneously retained packet. A source-date-safe result must not be promoted into a local-capture-safe research qualification.

At the July 31, 2025 12:00 UTC cutoff, all six MSFT/AAPL/SPY market price/volume calls fail closed because the identities are unavailable under the reader's local-capture policy. Their older price observations do exist: separate calls using the present capture cutoff and the historical trade-date window return 752 observations per symbol, from 2022-08-01 through 2025-07-30. They are usable only as reconstructed historical market context. No current estimate was reused as historical consensus.

## Market freshness and completeness

Present-window calls cover 2023-09-06 through 2026-09-04, within the requested three years. OHLC and volume date grids agree, with no returned null observations and no result-limit truncation.

| Symbol | Daily observations per field | Latest observation | Latest local price capture | Missing sessions in checked 2024–2026 calendar |
| --- | ---: | --- | --- | ---: |
| MSFT | 741 | 2026-09-04 | 2026-09-04T22:06:38.101698Z | 12 |
| AAPL | 743 | 2026-09-04 | 2026-09-04T22:00:18.511647Z | 10 |
| SPY | 741 | 2026-09-04 | 2026-09-04T22:08:37.125383Z | 12 |

All three lack August 17–21 and August 24–28. MSFT and SPY additionally lack August 31 and September 1. The latest endpoint is current for this declared cutoff, but the history has gaps. Calendar completeness before 2024 was not tested because the established local calendar starts in 2024.

Public metadata declares FMP/provider-native prices and currency units; adjustment and total-return semantics are not established. Corporate-action rows do not by themselves repair this provenance. These observations are daily bars, not executable quotes. Preserve the emitted local-capture, observed-row-horizon, adjustment, volume-unit and calendar warnings.

## Consensus and status pitfalls

AAPL's one retained FMP analyst-estimates capture is 2026-09-05T03:48:28.495245Z, hash 996a2295d9993c449b581531896759df51cd6d9ce18cac72edf276a4a668a832. It requested period=annual, page=0, limit=10. Ten fiscal-end dates span 2021-09-25 through 2030-09-27; these are forecast target periods, not ten capture vintages.

The normalization retains averages, lows/highs and EPS/revenue analyst counts, but currency is source-unspecified and fiscal_year/fiscal_period are null in public records. GAAP/adjusted basis is not established. The source snapshot explicitly says partial and carries fmp_source_currency_unspecified and fmp_analyst_estimates_may_be_truncated_at_limit_10. The public consensus call nevertheless returns no warnings and no result truncation. Result-limit completeness therefore does not prove source completeness.

data.get_dataset_status returned 25 company/market records. It describes control-plane captures, not per-issuer coverage. Some datasets report no_data/no_retained_outcome despite proven filing, identity, action or consensus rows. Conversely, current company-level capture status does not establish MSFT consensus. Use actual reader results and snapshot scope alongside status.

## FMP as a source for the gaps

The owner's suggestion is broadly right for standard structured inputs. Documentation establishes candidate capabilities; this audit did not test account entitlements or actual MSFT/AAPL responses.

| Gap | FMP candidate | What still needs validation |
| --- | --- | --- |
| Operating income, earnings, EPS, shares | /stable/income-statement | Exact fiscal periods, GAAP basis, amendment/filing linkage; [official documentation](https://site.financialmodelingprep.com/developer/docs/stable/income-statement) |
| Cash, investments and debt | /stable/balance-sheet-statement | Component definitions and lease inclusion; [official documentation](https://site.financialmodelingprep.com/developer/docs/stable/balance-sheet-statement) |
| Operating cash flow and cash PPE | /stable/cash-flow-statement | Cash PPE line versus broader capex, signs and quarter/YTD basis; [official documentation](https://site.financialmodelingprep.com/developer/docs/stable/cashflow-statement) |
| Product/segment revenue | /stable/revenue-product-segmentation | Microsoft Cloud/Azure disclosure coverage and segment changes; [official documentation](https://site.financialmodelingprep.com/developer/docs/stable/revenue-product-segmentation) |
| Earnings announcements | /stable/earnings, /stable/earnings-calendar | Event date/time, fiscal target, estimate/actual basis; [official earnings documentation](https://site.financialmodelingprep.com/developer/docs/stable/earnings-company) |
| Analyst expectations | /stable/analyst-estimates | Quarter versus annual coverage, currency/basis and authentic capture vintages |
| Detailed reported financial facts | /stable/financial-statement-full-as-reported, /stable/financial-reports-json | Required lease tags, commitments and table locators |
| Call transcripts | /stable/earning-call-transcript and /stable/earning-call-transcript-dates | Availability, source provenance and permitted excerpt reuse |
| Price gaps | /stable/historical-price-eod/full and separately documented adjustment variants | Exact missing dates and source-specific adjustment contract |

The last four endpoint groups are documented in the [official endpoint catalog](https://site.financialmodelingprep.com/developer/docs). The catalog does not establish arbitrary historical as-of consensus vintages. A new retrieval cannot establish what analysts expected on July 31, 2025. Prospective snapshots can provide genuine future comparisons after compatible captures accumulate.

FMP can supply many ordinary numbers, but does not eliminate the need for primary filing/release review for lease commitments, custom cloud metrics, guidance and citable evidence. Segment revenue is not automatically an Azure growth disclosure. Management guidance must remain separate from analyst estimates. Account access and source reuse rights remain unverified; no subscription change is proposed.

## Recommended next work

1. Expose the already-retained reviewed facts through a supported latest contract, preserving ratio functionality and complete provenance. Fix source completeness/warning propagation and distinguish capture freshness from per-issuer data coverage.
2. Resolve duration-context identity before claiming eight standalone quarters. Preserve annual, quarter and YTD distinctions, amendments, units and source locators.
3. Add operating income, cash PPE and cash/debt mappings, then the explicitly required lease and cloud facts. Reuse verified retained evidence where it exists. The current SEC path stores selected facts plus source hash metadata; this audit did not establish a recoverable full CompanyFacts-byte archive or filing-text archive. A hash is not a retrievable document.
4. Prepare bounded citable source sections and earnings-event/guidance access. Preserve private news-body boundaries. Reuse the sibling's existing MSFT source captures as a potential input only after their actual files/hashes and ownership are checked.
5. Prepare a finite MSFT/AAPL collection proposal only for remaining gaps, plus exact missing MSFT/AAPL/SPY price sessions. Existing zero-argument roster refreshes cannot be repurposed as a two-issuer manual command. No suitable fully implemented command covers all requested new datasets today.
6. Build reviewed calculations from explicit source operand IDs and compatible units/periods. The narrow OCF-minus-cash-PPE calculation is currently blocked by missing PPE facts. Keep it distinct from complete owner earnings.
7. The sibling app must separately add adapters beyond news.search. Warehouse data alone does not integrate the packet or qualify its models.

This task made no implementation changes, migrations or provider collections. No executable test suite was warranted for an audit-only artifact. The 68 actual calls, immutable reads, exact eight-fact comparison, complete filing pagination, historical unavailable cases and bounded calendar check are the validation obtained. Implementation acceptance tests in the pasted proposal remain future work.
