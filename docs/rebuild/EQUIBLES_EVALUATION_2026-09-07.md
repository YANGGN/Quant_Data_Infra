# Equibles API evaluation — September 7, 2026 UTC

Equibles is a useful transcript source on the configured free account. The sampled
history generally starts in 2020, with NVIDIA extending into 2019. Its structured
guidance extraction is sparse, sometimes stale, and has date defects; it is not
a complete historical guidance database. Prioritize transcripts, short interest,
buybacks, and ownership data, with explicit provenance and coverage.

This is an evaluation, not a canonical population or provider activation.

## Executed scope and evidence

- Tested AAPL, MSFT, NVDA, JPM, and WMT using the configured
  `EQUIBLES_API_KEY` through `quant_data.credentials.read_project_credential`.
  The credential value was never displayed or retained.
- Exactly **40 authenticated GET requests**, all HTTP 200; no retries, redirects,
  subscription changes, or recurring-unit actions. HTTP success includes some
  empty/unprocessed datasets and does not itself establish usable coverage.
- Requests ran from **2026-09-07T03:47:40Z to 03:50:58Z**.
  Response headers showed a limit of **100/day** and **60 remaining** at completion.
- One additional unauthenticated public OpenAPI download: 658,894 bytes,
  **133 path entries / 128 GET operations**. Alias paths mean these are not
  128 independent datasets. Reviewed the complete operation inventory and
  selected company datasets for live samples; did not execute the entire API.
- Retained authenticated response bytes total **764,849**. All 40 response
  SHA-256 digests were checked offline, and all 40 exact requests are distinct.
- No canonical database was opened or written. Registry, source, migrations,
  and existing company/FMP contracts were read to assess overlap. Existing
  uncommitted work was preserved; current live database row coverage was not
  independently inventoried.
- Private evidence: [attempt ledger](../../.local/equibles-evaluation-20260907/attempts.jsonl),
  [result ledger](../../.local/equibles-evaluation-20260907/results.jsonl),
  [machine-readable analysis](../../.local/equibles-evaluation-20260907/analysis.json),
  [public schema](../../.local/equibles-evaluation-20260907/openapi.json),
  [endpoint inventory](../../.local/equibles-evaluation-20260907/api-inventory.json).
  The adjacent `probe.py` is a task-specific capped evaluator, not a production
  collector or scheduled command. Its request budget is exhausted.

## 1. Transcripts and history

Endpoint discovery:
`GET /v1/stocks/{ticker}/investor-events?eventType=EarningsCall&limit=100&offset=0`.

Transcript text:
`GET /v1/stocks/{ticker}/earnings-calls/{fiscalYear}/{fiscalQuarter}/speakers?limit=200&offset=0`.

All five event lists returned `meta.hasMore=false`, so these are the full lists
exposed by that endpoint at capture time. Count only `hasTranscript=true`;
exclude scheduled future events. Metadata advertises 132 transcripts across
the five companies; actual text was sampled for the oldest and newest of each,
not downloaded for all 132.

| Company | Available fiscal-quarter span | Listed transcripts | Gaps within that span |
| --- | --- | ---: | --- |
| AAPL | FY2020 Q1–FY2026 Q3 | 27 | None in returned roster |
| MSFT | FY2020 Q2–FY2026 Q4 | 27 | None in returned roster |
| NVDA | FY2020 Q1–FY2027 Q2 | 29 | FY2020 Q3 |
| JPM | FY2020 Q1–FY2026 Q2 | 26 | None within span; FY2019 Q4 event exists without transcript |
| WMT | FY2020 Q4–FY2027 Q2 | 23 | FY2021 Q1/Q2, FY2022 Q1, FY2026 Q3 |

For AAPL/MSFT, that is roughly 6.5 years through July 2026; JPM/WMT are similar.
NVDA extends to the May 2019 FY2020 Q1 call. Fiscal years can run ahead of
calendar years. These are sampled company ranges, not a platform-wide guarantee.

All **10 fetched transcripts** returned nonempty text, matching row/turn totals,
and `hasMore=false`. They contained 33–89 speaker turns and approximately
41,000–67,000 text characters each. Reviewed content includes prepared remarks
and Q&A. Completeness here means complete relative to the API's reported turn
count, not independent word-for-word verification against recordings.

Speaker names are partially unresolved, particularly in old transcripts.
Historical samples lack audio timestamps; recent samples have them. Preserve
null names/times instead of fabricating them. Gate availability on
`hasTranscript`, because audio-derived speaker turns need not have a
`transcriptDocumentId`.

[Equibles earnings endpoint documentation](https://equibles.com/docs/api/endpoints/earnings)
describes event discovery, fiscal periods, pagination, and speaker identification.

## 2. Structured management guidance

`GET /v1/stocks/{ticker}/guidance` accepts no date, pagination, or limit parameters.
It returns management ranges, target fiscal periods, metric/basis/unit, source
quotes, filing-or-call provenance, and coverage metadata. This is management
guidance, separate from the FMP analyst estimate/consensus datasets.

| Company | Returned metric rows | Distinct provider-labelled source dates | Interpretation |
| --- | ---: | --- | --- |
| AAPL | 1 | 2021-10-28 | Stale and incomplete for current guidance |
| MSFT | 4 | 2021-09-30 | One old source, with a source-date defect |
| NVDA | 9 | 2021-10-31; 2026-08-26 | Two dates, not continuous five-year history |
| JPM | 4 | 2021-10-13; 2021-12-31 | Two old sources; only two documents examined |
| WMT | 20 | 2021-10-31; 2024-08-15; 2026-08-20 | Three dates, not continuous five-year history |

These dates are raw provider labels, not independently validated announcement
dates. Multiple metrics from one source do not represent multiple historical
guidance vintages.

AAPL's endpoint reports seven examined documents through July 2026 but returns
only the 2021 gross-margin range. MSFT reports only one examined source. The
current AAPL and MSFT event endpoints both return `guidanceStatus=notExtracted`.

The retained AAPL July 2026 transcript contains a 47%–48% gross-margin outlook
and other forward guidance in its CFO remarks, absent from the guidance response.
The retained MSFT July 2026 transcript also contains next-quarter segment,
total-revenue, expense, and capex guidance absent from its four-row response.
These examples establish an internal coverage gap between supplied transcript
text and structured extraction; the numerical forecasts were not separately
audited against company recordings.

Practical result: the text supports reconstructing a deeper guidance history,
but that requires our own source-linked extraction and review. Do not represent
the existing structured endpoint as a complete ready-to-ingest history.

[Equibles guidance documentation](https://equibles.com/docs/api/endpoints/fundamentals)
describes source quotes, accounting basis, coverage, and actual-versus-guidance
comparisons. Preserve these distinctions rather than mixing analyst forecasts,
management ranges, and realized actuals.

## 3. Confirmed date defects and integration constraints

1. **NVDA FY2020 Q1:** both event and transcript metadata say
   `callDate=2019-04-30`. NVIDIA's official release says the earnings call
   was **May 16, 2019**. The returned transcript discusses the corresponding
   quarter's $2.2 billion revenue. Treat the provider date as disputed.
   [NVIDIA's original release](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-first-quarter-fiscal-2020).

2. **MSFT FY2022 Q1 guidance:** its standalone guidance rows carry
   `filedDate=2021-09-30` and `form=Earnings Call`, while Equibles' own
   corresponding event gives **2021-10-26**. Microsoft's release confirms
   October 26 as the announcement date and September 30 as period end.
   [Microsoft's original release](https://www.microsoft.com/en-us/Investor/earnings/FY-2022-Q1/press-release-webcast).

3. Sparse/empty extraction is not absence of business activity.
   `notExtracted`, withheld older KPI records, zero examined filings, and
   `missReason=tagged-xbrl` have different meanings. AAPL's reassuring
   guidance staleness message does not establish that recent calls offered
   no guidance; the supplied transcript contradicts that interpretation.

4. A historical event/settlement/target date does not prove when information
   became available. Preserve this evaluation's capture timestamps. Date-only
   events represented by midnight must not gain invented intraday precision.
   Transcripts also need publication/capture timing distinct from call start.

5. Short-interest shares and institutional positions are documented as
   adjusted to today's split basis. Preserve that adjustment convention and
   capture version. Settlement dates and 13F report dates must not become
   same-day availability timestamps in backtests.

6. The sampled insider transaction rows omit filing dates/accessions and
   source URLs. This endpoint alone does not establish historical
   disclosure-time availability or a robust filing-based event identity.
   Ownership summaries likewise lack row-level filing availability.

7. Calls can duplicate guidance from a release. For example, NVDA's latest
   operating-expense guidance appears as both a call and an 8-K source.
   Preserve both evidence links without counting them as independent forecasts.
   Missing basis stays unspecified; percentages are not dollar amounts.

## 4. Valuable additions, based on actual responses

| Candidate | Live result | Recommended priority and limits |
| --- | --- | --- |
| Earnings transcripts and event metadata | Ten complete transcript samples; 132 advertised across five names | **First.** Directly fills the FMP transcript gap. Retain speaker turns, unresolved identities, fiscal labels, source links, hashes and capture time. Validate disputed dates. |
| FINRA short interest | NVDA: **159 observations, 2020-01-15–2026-08-14**, no more pages | **First.** Positioning, changes and days to cover are distinct from existing price/fundamental data. Requires split-basis and publication-time handling. |
| Buyback programs and spending | AAPL: one program, **15 annual periods FY2011–FY2025**, 12 quarters through June 2026, and YTD figures | **First.** Program authorization and execution add context beyond cash-flow repurchase totals. Some historical amount/share fields are null; 15 periods does not mean 15 nonmissing observations for every metric. |
| Institutional holdings/activity | AAPL June 2026: five buyers and five sellers sampled; response reports 2,793 buyers and 3,047 sellers | **Next.** Manager-level positioning, with identity and filing-lag checks. This was a top-5-per-side sample, not complete holdings ingestion or a historical-depth test. |
| SEC fails to deliver | NVDA: **136 settlement records, 2026-01-02–2026-08-14**; no more pages in requested window | **Next.** Supply/settlement research. Response declares full coverage from 2017-06-15; older history was not fetched. Outstanding balances are not daily new short sales. |
| Insider transactions | Five recent AAPL transactions, including open-market/plan/security-type flags | **Next, after provenance work.** Useful insider activity; transaction date alone is inadequate for historical as-of use. More pages exist; history depth was not tested. |
| Executive changes/compensation | MSFT: two board changes, Dec 2025–May 2026; AAPL: 16 executive-year rows across FY2023–FY2025 | **Second priority.** Governance context. Executive-change sources and effective dates are useful; imported proxy coverage may lag disclosures. |
| Federal contract awards | Five MSFT awards dated Apr–Jun 2026; more pages exist | **Second priority.** Potential government-demand exposure. Awards are not recognized revenue; company/subsidiary coverage needs a separate check. |
| AI call themes, tone and briefs | NVDA: eight insight records, Nov 2024–Aug 2026; MSFT: one current brief despite requesting eight | **Optional derived research.** Keep separate from reported company facts. Eight insights is the requested maximum, not proven archive depth. |
| Structured guidance | 38 metric rows from only nine distinct company/source-date combinations | **Retain as incomplete evidence only.** Reconstruct reviewed guidance from transcripts if history is required. |
| Company KPIs | MSFT and WMT both empty; older extractions withheld pending revalidation | **Defer.** The tested API does not currently deliver verified KPI series for these names. |
| Non-GAAP reconciliation bridges | NVDA and MSFT empty; `filingsExamined=0` | **Defer.** Both samples are unprocessed, not evidence of no reconciliations. |
| Debt instruments/covenants | MSFT has no returned instruments/totals; 40 filings unprocessed | **Defer.** Strong concept but no usable sample here. |
| Customer concentration | NVDA REST response empty with `missReason=tagged-xbrl` | **Defer REST ingestion for this case.** Docs say the MCP counterpart carries the tagged-XBRL path; REST does not return those figures. |

The 38 guidance rows are 1+4+9+4+20. The nine source-date combinations are
1+1+2+2+3; source dates are not reliable publication vintages.

## 5. Remaining API families and database overlap

The downloaded schema also exposes normalized filings/search, revenue
breakdowns, corporate actions, ETFs/fund holdings, advisers, congressional
disclosures, IPOs, FDA catalysts, ATM offerings, going-concern disclosures,
IR news/events/slides, short volume/off-exchange volume, CFTC positioning,
CBOE sentiment, FRED macro series, index changes/forecasts, prices/options,
technical indicators, and account-owned portfolios/web feeds.

Prioritize unique company evidence over duplicating our existing SEC/FMP
statements, analyst history, price series, macro collectors, and news.
CFTC positioning, CBOE put/call ratios, short/off-exchange volume, and
index-membership history merit a later bounded evaluation; their live
entitlements and historical depth were not tested here. Account write
endpoints and hosted web-feed creation are outside this evaluation.

The project already has company guidance/versioning and expectation structures,
but those require resolved source URLs, target periods, metrics, review state,
and availability semantics. Do not force uncertain Equibles rows into them.
The FMP-native research/analyst tables are not generic Equibles containers.

For a subsequent integration, add explicit provider evidence and typed dataset
ownership. Company disclosures, transcripts, guidance, ownership, and buybacks
belong to company-oriented research; short/settlement series require an explicit
market ownership decision. Preserve current ownership boundaries, source-native
payloads, immutable captures, replay behavior, and reviewed derived results.
This report implements no registry/migration/publisher changes.

## 6. Recommendation and limits

Start with a reviewed transcript integration, then short interest and buybacks.
Keep Equibles as the selected platform while treating its structured guidance as
partial extraction. Deeper guidance requires extraction from retained transcript
text with fiscal-period, unit, accounting-basis, source-date and as-of checks.

Free access worked for every tested endpoint, but 100 requests/day would make
a multi-year backfill across hundreds of names slow. At one request per complete
transcript, 500 names × 4 quarters × 6 years is approximately 12,000 requests
or at least 120 days, before discovery and extra pages. That is an illustration,
not an authorized population. All ten tested transcripts fit one 200-turn page.
No plan upgrade was performed.

The earlier review found no explicit general cache prohibition in Equibles'
public terms, but did not establish perpetual archival/redistribution rights.
Confirm retention rights for a planned persistent archive; keep any public
redistribution separate. [Equibles terms](https://equibles.com/legal/terms).

Validation was limited to authenticated read samples, retained-response integrity,
pagination/completeness checks, source/date spot checks, schema review and
repository-contract comparison. It was not a platform-wide coverage census,
independent transcript accuracy audit, canonical safety certification, or
historical point-in-time dataset validation. No application/test suite was run
because no application code or canonical schema changed.
