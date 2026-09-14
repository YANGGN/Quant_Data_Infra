# Local Agent Tools

This is the supported interface for an agent in another project on this same
computer. It is a local command that runs inside this project; it does not
start a server, expose a URL, or grant a direct SQLite connection.

Use the launcher from WSL/Linux:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools list
```

An agent running from a Windows-native project must still execute the same
Ubuntu/WSL launcher rather than using Windows Python or opening the stores:

```powershell
wsl.exe -d Ubuntu -- /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools list
```

The command derives this project's root itself. Do not copy its database paths,
set database-related environment variables, or invoke it with a registry,
project-root, store, SQL, provider, or credential option; none are supported.

## Cross-project quick start

The absolute launcher path above is the complete integration boundary. An
agent does not need this repository as its working directory and must not add
this package to the consuming project's imports. Use this sequence:

1. Run `manifest` to obtain the machine-readable inventory and schemas.
2. Run `list` for a compact logical-tool inventory.
3. Select the highest advertised `major.minor.patch` version for the logical
   tool, then run `describe TOOL --tool-version VERSION`.
4. Send one strict JSON envelope to `call` on standard input, with that exact
   `tool_version`. The top-level compatibility default is not the latest version.
5. Parse standard output as JSON even when the process exits nonzero.
6. Keep the complete response, receipt, warnings, truncation, and lineage with
   any downstream result.

At the earlier macro-database-expansion checkpoint, registry `2.68.0` exposed 74
logical names. Its source SHA-256 is
`9b59f6b643e4cff7390559763c8532215ac9927a1f3119870385127af3a6a27e`;
catalog `2.26.0` has SHA-256
`fcfb29de2c2138995918e40c603704a0b2df4c17b6c3229312734bb46b0f2a28`.
This snapshot is informative; `manifest` and `describe` remain the runtime
authority if the project advances.

## Retained transcripts and structured extractions

These three local tools are available at version `1.0.0`:

| Tool | Purpose | Page limit |
| --- | --- | --- |
| `company.search_transcripts` | Find retained captures and whether a structured extraction is available | 50 default, 100 maximum captures |
| `company.get_transcript` | Read original provider text and speaker fields for an exact capture | 50 default, 100 maximum speaker turns |
| `company.get_transcript_extraction` | Read the original structured draft and its independent assessments | 10 default, 20 maximum assessments |

Start by searching for an exact uppercase provider ticker. The optional
`fiscal_year` and `fiscal_quarter` filters use the provider's fiscal labels.
Omit the ticker to page through all retained captures. Search returns every
matching capture, ordered by symbol, fiscal year, fiscal quarter, capture time,
and capture ID; multiple captures of one fiscal period remain separate.

From any WSL/Linux project directory:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call <<'JSON'
{"api_version":"1.0","tool":"company.search_transcripts","tool_version":"1.0.0","arguments":{"ticker":"AAPL","limit":10}}
JSON
```

Read the returned `capture_id`, then pass it to either detail tool. Replace
the placeholder below with that ID; it is not a literal example capture.

```json
{"api_version":"1.0","tool":"company.get_transcript","tool_version":"1.0.0","arguments":{"capture_id":"<capture_id from search>","limit":100}}
```

```json
{"api_version":"1.0","tool":"company.get_transcript_extraction","tool_version":"1.0.0","arguments":{"capture_id":"<capture_id from search>","limit":20}}
```

Both detail tools use the capture ID, so raw evidence and structured output
cannot silently drift to different calls. Each result uses the existing
`result.records` contract: a `record_type` and a `fields` array of
`{"name": ..., "value": ...}` objects. Convert that array to a name/value map.
Nested source documents remain JSON strings in these explicitly named fields:

| Record type | Content |
| --- | --- |
| `transcript_capture` | Capture ID, symbol, fiscal labels, turn/page counts, capture time, latest eligible analysis ID, extraction availability, automatic quality and review status |
| `transcript_metadata` | Capture details and `event_json`, the original provider event object |
| `transcript_turn` | Original `text`, `raw_turn_json` with every original speaker/turn field, source page checksum/reference, one-based turn number and `turn_id` |
| `transcript_extraction` | `structured_json`, the complete original structured draft; model, prompt/schema versions, input/response hashes, availability times and quality/review status |
| `transcript_assessment` | `assessment_json`, kind, evaluator, outcome, evidence hash and availability time |
| `transcript_extraction_status` | A retained capture with no eligible structured extraction and an explicit reason |

Decode `structured_json`, `assessment_json`, `raw_turn_json`, and
`event_json` as JSON when needed. For example, in a consuming Python project:

```python
import json
import subprocess

launcher = "/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools"

def call(tool, arguments):
    process = subprocess.run(
        [launcher, "call"], input=json.dumps({
            "api_version": "1.0", "tool": tool,
            "tool_version": "1.0.0", "arguments": arguments,
        }), text=True, capture_output=True, check=False,
    )
    response = json.loads(process.stdout)
    if process.returncode:
        raise RuntimeError(response)
    return response  # Retain the receipt, warnings and lineage too.

def fields(record):
    return {field["name"]: field["value"] for field in record["fields"]}

found = call("company.search_transcripts", {"ticker": "AAPL", "limit": 1})
captures = found["result"]["records"]
if captures:
    capture_id = fields(captures[0])["capture_id"]
    saved = call("company.get_transcript_extraction", {"capture_id": capture_id})
    for record in saved["result"]["records"]:
        if record["record_type"] == "transcript_extraction":
            metadata = fields(record)
            original_draft = json.loads(metadata["structured_json"])
            quality_status = metadata["automatic_quality_status"]
```

Pagination: inspect `result.truncation.next_cursor` (also repeated as the
`next_cursor` metric in `result.diagnostics[0].metrics`). If non-null,
call the same tool with the
same filters, capture ID and time mode plus that `cursor`. The limit may
change within its bound. Continue until the cursor is null to retrieve all
captures, speaker turns, or assessments. Detail pages repeat their metadata;
extraction pages repeat the original draft while paging its assessments.
`truncation.returned_count` and `returned_page_items` count page items,
excluding the repeated metadata/draft. A cursor pins the selection cutoff;
an extraction cursor also pins the selected analysis ID. Cursors are opaque,
query-specific continuation tokens, not access credentials.

The default mode is `latest`, using the host's current UTC time.
For a fixed cutoff, pass both `"mode":"as_of"` and a timezone-aware
`"as_of":"2026-09-13T23:00:00Z"`. Date-only, timezone-free, and future
cutoffs are rejected. Raw evidence is eligible at retained capture time;
outputs and assessments are eligible at their recorded completion times,
with source eligibility also checked. A draft can be visible before its
later assessment. `published_at` is retained for audit but does not replace
the existing completion-time availability contract. Fiscal labels and
provider call dates do not establish historical public availability.

No matching capture returns `status: "not_established"` and no records.
A retained capture without an eligible extraction returns the explicit
`transcript_extraction_status` record. Search coverage is per capture;
it does not certify that every expected quarter has been collected.

The original draft is returned unchanged, including drafts whose automatic
quality status is `blocked`. `unassessed` means no eligible automatic
assessment exists. `review_status` is `unreviewed` or
`review_recorded`; the latter only means an independent review exists.
Always inspect `latest_review_outcome` and the assessment documents.
A failed review does not approve, rewrite, or replace the original draft.
Source `turn_id` values (`t1`, `t2`, …) join structured evidence references
to the raw-turn pages.

Each call uses the existing coordinated immutable company reader. It makes
zero provider/model requests and performs no collection, extraction,
migration, or canonical write. Reads fail closed if the store cannot be read
immutably or its physical lock is busy. Selected raw pages or structured
output plus selected assessments are bounded to 4 MiB; event metadata is
bounded to 64 KiB, the final response to 8 MiB, and calls retain the host
deadline. Raw page hashes and selected turn coverage are checked before
returning a response. A raw page is an indivisible validation unit, even
when the requested speaker-turn page is smaller.

## Current price workflow

For new integrations, use the versions in [Latest contracts](#latest-contracts)
and confirm them with the runtime manifest. The current composition is:

| Task | Explicit contract |
| --- | --- |
| Retrieve typed OHLC | `market.get_price_series@2.0.0` |
| Compute trailing / forward price returns | `market.get_returns@2.1.0` / `market.get_forward_returns@2.1.0` |
| Compute technical indicators | `market.technical_indicators@2.8.0` |
| Audit / summarize typed returns | `data.quality_audit@2.1.0` / `timeseries.describe@2.1.0` |
| Retrieve ETF allocator features | `portfolio.get_etf_allocator_snapshot@2.0.0` |

FMP full-EOD `close` is already split-adjusted and excludes dividend
adjustments. Use the retained value unchanged: do not apply another split
factor, join split history to rescale it, or substitute dividend-adjusted
`adjClose`. The producer establishes this basis only when its retained capture
evidence matches the documented endpoint and parser. Open, high, low, empty
histories, and unsupported capture bindings remain unestablished under this
close-specific convention.

Select the matching [metadata-aware analytical versions](#fmp-close-metadata-successors-september-13-2026)
when composing tools. Pass complete typed series, including metadata, audits,
quality flags, provenance, and lineage. Do not relabel an older tool's output or
remove metadata to make it pass an older consumer. If a required version is
absent from the runtime manifest, report the contract mismatch rather than
silently falling back.

For ETF work, start with the [current complete-roster example](#etf-allocator-snapshot).
Check each feature's value, missing reason, and readiness. Preserve collection
and adjustment-vintage warnings: numerical readiness does not certify
historical publication timing or a common adjustment vintage. Portfolio policy,
volatility selection, approval, and execution remain in the consuming app.

## Browser Inspector

For local visual inspection of the same current manifest and canonical data,
start the loopback-only UI from this project:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-inspector
```

Then open the printed `http://127.0.0.1:8765/` URL. Data views remain fixed
and bounded. The Agent Tools section presents each current logical tool once,
pinned to its highest advertised semantic version. It generates request
controls from that latest schema, shows lifecycle and workload bounds, and
preserves the complete response in a bounded nested preview plus a complete
paged strict-JSON view. Typed time series returned by one tool can be reused
by another tool within that browser session.

Use the Current news shortcut for a bounded latest `news.search@2.3.0`
table with text, symbol, source, date, and cursor filters. It exposes retained
headline metadata and truncation through the public contract without exposing
article bodies or raw evidence. The advanced tool view retains lineage and
warnings. The Inspector binds only to loopback and has no SQL, database-path,
credential, provider-request, ingestion, scheduler, or write control.
Use `/data-status` for retained capture/outcome freshness across registered
datasets and fixed news sources. It does not probe providers, credentials, or
scheduler processes. `/healthz` reports Inspector process liveness only; it
does not claim data freshness or provider health.

The linked [actual-data audit](rebuild/CURRENT_TOOL_ACTUAL_DATA_AUDIT_2026-08-28.md)
is a pre-v2.7, 119-route checkpoint: it called all 119 routes separately. All
54 successors completed successfully; consult `manifest` for the current route count.
49 were semantically exercised with retained canonical data or typed series
derived from it. Five research/forecast routes were interface-validated only
because genuine retained event, signal, prediction, model-design, or strategy
inputs are not yet available. Empty and `not_established` results remain valid
data-dependent outcomes.

## Consumer authority

`manifest` is the machine-readable consumer authority. It is generated by the
active read-only dispatcher and contains the currently active tool inventory,
schemas, examples, limits, supported semantic versions, assumptions, and
lifecycle information.

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools manifest
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools list
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.get_price_series --tool-version 2.0.0
```

Use `list` for a compact inventory. Before every new integration, use
`manifest` to identify the semantic maximum of the selected tool's
`versions` array (or its sole `version`), then use
`describe TOOL --tool-version VERSION`. It returns the exact input and output
schemas, examples, limits, and version information for that latest contract.
Do not parse
`config/system_registry.json` or the generated schema files as a cross-project
integration interface; they are project-internal sources used to produce the
manifest.

Always pass the selected latest version to both `describe` and `call`,
including when that version is `1.0.0`. Do not omit `tool_version`: the
manifest's top-level `version` is a compatibility default and is not a
latest-version alias. Historical versions remain callable for explicit compatibility work.
Current examples and the Browser Inspector select the latest versions;
the dated ETF version 1 section below is preserved audit evidence only.

## Latest contracts

This is the latest-only projection verified through the public launcher for
registry `2.86.0`, catalog `2.32.0`: 80 logical tools and 169 versioned contracts.
If the registry advances, the semantic maximum advertised by the runtime
`manifest` overrides this checkpoint.

| Logical tool | Latest version |
| --- | --- |
| `macro.search_series` | `2.0.0` |
| `macro.describe_series` | `2.0.0` |
| `macro.get_series` | `2.0.0` |
| `macro.get_release_calendar` | `2.0.0` |
| `macro.get_intraday_releases` | `1.0.0` |
| `macro.release_surprises` | `1.0.0` |
| `macro.revision_analysis` | `2.0.0` |
| `macro.align_us_recessions` | `1.0.0` |
| `macro.standardize_surprises` | `2.0.0` |
| `macro.get_liquidity_snapshot` | `2.0.0` |
| `macro.get_liquidity_impulse` | `2.0.0` |
| `macro.get_credit_conditions` | `2.0.0` |
| `macro.regime_snapshot` | `2.0.0` |
| `timeseries.transform` | `2.1.0` |
| `timeseries.describe` | `2.1.0` |
| `timeseries.align` | `2.1.0` |
| `timeseries.correlation` | `2.1.0` |
| `econometrics.regression` | `3.1.0` |
| `econometrics.stationarity` | `2.2.0` |
| `econometrics.rolling_regression` | `2.2.0` |
| `econometrics.structural_breaks` | `2.1.0` |
| `econometrics.local_projection` | `1.0.0` |
| `company.search_issuers` | `1.0.0` |
| `company.search_filings` | `2.0.0` |
| `company.get_fundamentals` | `2.1.0` |
| `company.get_corporate_actions` | `1.0.0` |
| `company.get_share_count_history` | `2.0.0` |
| `company.get_earnings_calendar` | `1.0.0` |
| `company.get_consensus_history` | `1.0.0` |
| `company.get_guidance_history` | `1.0.0` |
| `company.get_estimate_revisions` | `1.0.0` |
| `company.get_earnings_setup` | `1.0.0` |
| `energy.get_electricity_retail_sales` | `2.1.0` |
| `energy.get_weekly_fundamentals` | `2.1.0` |
| `market.search_instruments` | `2.0.0` |
| `market.get_available_ticker` | `1.0.0` |
| `market.get_price_series` | `2.0.0` |
| `market.get_volume_series` | `1.0.0` |
| `market.get_returns` | `2.1.0` |
| `market.get_forward_returns` | `2.1.0` |
| `market.technical_indicators` | `2.8.0` |
| `market.cross_sectional_performance` | `2.2.0` |
| `rates.get_funding_conditions` | `2.0.0` |
| `rates.get_repo_facility_usage` | `2.0.0` |
| `rates.curve_analytics` | `2.0.0` |
| `options.search_captures` | `2.0.0` |
| `options.search_contracts` | `2.0.0` |
| `options.get_surface_snapshot` | `2.0.0` |
| `options.surface_diagnostics` | `1.0.0` |
| `options.screen_contracts` | `1.0.0` |
| `options.strategy_scenario` | `1.0.0` |
| `research.point_in_time_panel` | `2.1.0` |
| `data.get_dataset_status` | `1.0.0` |
| `data.quality_audit` | `2.1.0` |
| `research.event_study` | `2.1.0` |
| `alpha.signal_diagnostics` | `2.1.0` |
| `research.walk_forward_backtest` | `2.1.0` |
| `research.robustness_suite` | `2.1.0` |
| `stats.distribution_diagnostics` | `2.0.0` |
| `stats.covariance_matrix` | `2.0.0` |
| `stats.bootstrap_confidence_interval` | `2.0.0` |
| `stats.principal_components` | `2.0.0` |
| `stats.multiple_testing` | `2.1.0` |
| `forecast.evaluate` | `2.1.0` |
| `news.search` | `2.3.0` |
| `news.get_source_status` | `1.0.0` |
| `news.get_item_history` | `1.0.0` |
| `news.story_clusters` | `1.0.0` |
| `news.entity_coverage` | `1.0.0` |
| `news.attention_metrics` | `1.0.0` |
| `news.classify_events` | `1.0.0` |
| `news.headline_sentiment` | `1.0.0` |
| `research.news_event_impact` | `2.0.0` |
| `research.liquidity_credit_state` | `2.0.0` |
| `portfolio.get_etf_allocator_snapshot` | `2.0.0` |
| `price_realtime` | `1.0.0` |
| `company.get_research_inputs` | `1.0.0` |
| `company.search_transcripts` | `1.0.0` |
| `company.get_transcript` | `1.0.0` |
| `company.get_transcript_extraction` | `1.0.0` |

Start a market workflow with `market.get_available_ticker`. A ticker is
included only when its FMP/provider-native Stage 10 instrument has at least one
current daily-price row that the verified latest reader can retrieve. The
result is the current retained-data discovery set—not a live universe or a
historical point-in-time universe. Calling it with `{}` returns all matching
tickers up to the 10,000-record platform bound; an optional `limit` can lower
that bound. Then select a returned `ticker` for
`market.get_price_series`.

Each returned record has these stable fields:

- `ticker`
- `instrument_id`
- `asset_type`
- `display_name`
- `exchange_code`
- `provider`
- `currency_segment`
- `price_variant`

## FMP quotes and company research inputs

The local launcher enables `price_realtime@1.0.0` with
`{"ticker":"MSFT"}`. Each call makes one FMP quote request, with no retry
or store writes. Inspect `price`, `quoted_at`, `captured_at`,
`quote_age_seconds`, nullable bid/ask, and warnings. This is the latest
reported trade. Currency is `SOURCE_UNSPECIFIED` when FMP omits it.
Generic server/Inspector hosts leave this live capability disabled.

For statement-level inputs use `company.get_research_inputs@1.0.0`:

```json
{"api_version":"1.0","tool":"company.get_research_inputs","tool_version":"1.0.0","arguments":{"cik":"0000789019","endpoints":["income-statement"],"period":"quarter","limit":12}}
```

MSFT is CIK `0000789019`; AAPL is `0000320193`. Endpoint filters are
`income-statement`, `balance-sheet-statement`, `cash-flow-statement`,
`financial-statement-full-as-reported`, `revenue-product-segmentation`,
`analyst-estimates`, and `earnings`. Request one endpoint and period at a
time when assembling a packet, and inspect truncation; the result maximum
is 100 rows. Source fields are preserved in `payload_json`, alongside
period, fiscal labels, currency, capture time, content hash and row pointer.

`company.get_fundamentals@2.1.0` retains its net-margin and
liabilities-to-assets ratio contract. The new tool supplies additional
statement and earnings inputs and distinguishes annual FY from quarterly
Q4. As-of uses local capture time; new data does not become a contemporaneous
2025 snapshot. Preserve currency, accepted-date, period and source-limit
warnings. See the [contract](rebuild/FMP_RESEARCH_INPUTS_CONTRACT_2026-09-06.md)
and [handoff](rebuild/FMP_RESEARCH_INPUT_HANDOFF_2026-09-06.md).

## Calling a tool

`call` accepts exactly one strict UTF-8 JSON envelope on standard input and
writes exactly one strict JSON response to standard output. It accepts no
request filename or command-line arguments.

```bash
printf '%s\n' '{"api_version":"1.0","tool":"market.get_available_ticker","tool_version":"1.0.0","arguments":{}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"market.search_instruments","tool_version":"2.0.0","arguments":{"query":"Apple","asset_type":"equity","cursor":null,"limit":100}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"market.get_price_series","tool_version":"2.0.0","arguments":{"ticker":"SPY","mode":"latest","as_of":null,"date_only_policy":"completed_date","limit":1000}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"market.get_volume_series","tool_version":"1.0.0","arguments":{"ticker":"SPY","mode":"latest","as_of":null,"date_only_policy":"completed_date","limit":1000}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"macro.search_series","tool_version":"2.0.0","arguments":{"query":"gdp","limit":100}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"macro.describe_series","tool_version":"2.0.0","arguments":{"series_id":"macro.gdp.real_qoq_saar_pct"}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"macro.get_series","tool_version":"2.0.0","arguments":{"series_id":"macro.gdp.real_qoq_saar_pct","mode":"as_of","as_of":"2026-07-31T23:59:59Z","date_only_policy":"completed_date","limit":100}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"macro.get_release_calendar","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","limit":100}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"company.search_filings","tool_version":"2.0.0","arguments":{"query":"0000320193","as_of":null,"cursor":null,"limit":100}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"company.get_share_count_history","tool_version":"2.0.0","arguments":{"cik":"0000320193","mode":"latest","as_of":null,"date_only_policy":"completed_date","limit":1000}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call

printf '%s\n' '{"api_version":"1.0","tool":"news.search","tool_version":"2.3.0","arguments":{"query":"","symbols":["AAPL"],"source_ids":["fmp_stock_latest","alpaca_benzinga"],"mode":"latest","as_of":null,"date_only_policy":"completed_date","start_date":null,"end_date":null,"cursor":null,"limit":100}}' \
  | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
```

`company.search_filings@2.0.0` requires one exact ten-digit SEC CIK. Follow
its opaque `cursor` until the result is terminal; cursors are bound to the CIK
and cutoff and cannot be reused for another search. This avoids the legacy
500-row dead end without computing an unbounded total count.

`company.get_share_count_history@2.0.0` also requires one exact ten-digit CIK.
It returns only the reviewed SEC outstanding, weighted-average basic, and
weighted-average diluted share metrics. Instant and weighted-average facts
remain distinct; the tool does not split-adjust values or infer missing
metrics.

### Current news and analytics quick start

Use `news.search@2.3.0` for the current fixed-source reader with opaque
keyset pagination. Historical contracts are compatibility-only and are not
shown here. The current version accepts optional `query`, `symbols`,
`source_ids`, `start_date`,
`end_date`, `mode`, `as_of`, `date_only_policy`, `cursor`, and `limit`.
`as_of` is required only in `as_of` mode; `limit` is 1 through 500.

The fixed source IDs are `fmp_stock_latest`, `fmp_press_releases`,
`fmp_general`, `fed_press`, `ecb_press`, `bea_news`, `eia_press`, and
`alpaca_benzinga`, `finviz`, and `financialjuice`. These are the accepted
`source_ids` in version 2.3. Finviz time-only/month-day publication labels
retain unknown precision. Versions 2.1 and 2.2 retain their original source set. The inactive
legacy `fmp_news_articles` relation is private evidence, not a public source.

~~~bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools manifest
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe news.search --tool-version 2.3.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe news.get_source_status --tool-version 1.0.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe news.get_item_history --tool-version 1.0.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe news.story_clusters --tool-version 1.0.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe news.entity_coverage --tool-version 1.0.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe news.attention_metrics --tool-version 1.0.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe news.classify_events --tool-version 1.0.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe news.headline_sentiment --tool-version 1.0.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe research.news_event_impact --tool-version 2.0.0

printf '%s\n' '{"api_version":"1.0","tool":"news.search","tool_version":"2.3.0","arguments":{"query":"","symbols":["AAPL"],"source_ids":["fmp_stock_latest","alpaca_benzinga"],"mode":"latest","as_of":null,"date_only_policy":"completed_date","start_date":null,"end_date":null,"cursor":null,"limit":100}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"news.get_source_status","tool_version":"1.0.0","arguments":{"source_ids":[]}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"news.story_clusters","tool_version":"1.0.0","arguments":{"query":"","symbols":["AAPL"],"source_ids":[],"mode":"latest","as_of":null,"date_only_policy":"completed_date","start_date":null,"end_date":null,"limit":100,"window_hours":24}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
~~~

To obtain every page, read `result.truncation.next_cursor` and send it back
as `cursor` while keeping every other search field unchanged. The cursor is
opaque and query-bound; it cannot be reused with another query. A new capture
can change later `latest` pages, so use `as_of` when a retained local-capture
cutoff matters.

`news.get_source_status` reports retained attempt, outcome, capture, and
coverage metadata; it is not a live provider-health probe. `news.get_item_history`
accepts one `article_id` returned by search and exposes its bounded immutable
version/capture-membership history. Use an `article_version_id` from that
history together with a stable Stage 10 `instrument_id` for
`research.news_event_impact`:

~~~bash
printf '%s\n' '{"api_version":"1.0","tool":"news.get_item_history","tool_version":"1.0.0","arguments":{"article_id":"ARTICLE_ID_FROM_SEARCH","limit":100}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"research.news_event_impact","tool_version":"2.0.0","arguments":{"article_version_id":"ARTICLE_VERSION_ID_FROM_HISTORY","instrument_id":"STAGE10_INSTRUMENT_ID","pre_observations":5,"post_observations":5}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
~~~

`news.story_clusters` produces candidate clusters only from exact canonical
URLs or normalized headlines within a bounded time window; it is not canonical
deduplication. `news.entity_coverage` aggregates provider symbols only:
they are not named-entity recognition and do not establish historical
instrument identity. `news.attention_metrics` is descriptive bucket/source
coverage with explicit zero buckets and can return `insufficient_history`.
`news.classify_events` and `news.headline_sentiment` use fixed, transparent
rule/lexicon versions over retained headline metadata; their labels are not
provider facts, model predictions, recommendations, or trading signals.

`research.news_event_impact` is a retrospective observed close-to-close
window beginning at the next observed session after the local capture date.
It is not a causal estimate, abnormal return, intraday study, session-calendar
claim, or forecast, and it can honestly return `not_established` when the
selected version, current symbol mapping, or retained market window is
insufficient.

All news tools expose retained headline metadata, capture availability,
lineage, warnings, and truncation only. They never return provider raw bytes
or article bodies, never fetch from a provider, and remain read-only. An empty,
`unavailable`, `insufficient_history`, or `not_established` result is an
honest data-dependent outcome.

The operator-only manual batch is
`python3 scripts/refresh_current_news.py`. It uses `FMP_API_KEY` for FMP and
`ALPACA_API_KEY` plus `ALPACA_API_SECRET` for Alpaca; absent credentials yield
an `unavailable` source outcome without a prompt. The official RSS feeds need
no credential. The bounded 2026-08-30 16:00 UTC proof made 20 requests with no
retry: all eight source steps succeeded, including 13 Alpaca/Benzinga symbol
batches. The corresponding per-user systemd service and timer were linked,
enabled, and started on 2026-08-30. The timer runs hourly at `:10` UTC with no
retry or catch-up. Other agents may inspect it with `systemctl --user status`
or `list-timers`, but must not manually trigger, retry, broaden, reinstall,
disable, or repurpose it.

### Investment-analysis latest quick start

The following examples use each logical tool's current latest contract. Each
is one complete strict-JSON envelope that can be sent to the same `call`
command shown above.

~~~bash
printf '%s\n' '{"api_version":"1.0","tool":"macro.get_release_calendar","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","limit":100,"start_date":null,"end_date":null,"event_name":null,"cursor":null}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"macro.revision_analysis","tool_version":"2.0.0","arguments":{"series_id":"macro.gdp.real_qoq_saar_pct","start_date":null,"end_date":null,"limit":500}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"macro.standardize_surprises","tool_version":"2.0.0","arguments":{"kind":"us_cpi_headline_mom","release_stage":null,"start_date":null,"end_date":null,"limit":500}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"rates.get_funding_conditions","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","observation_date":null,"spread_left":"EFFR","spread_right":"SOFR","limit":20}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"rates.get_repo_facility_usage","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","observation_date":null,"limit":20}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"rates.curve_analytics","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","observation_date":null,"spread_left_tenor":"10Y","spread_right_tenor":"2Y","limit":20}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"macro.get_liquidity_snapshot","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","observation_date":null,"limit":20}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"macro.get_liquidity_impulse","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","start_date":"2026-01-01","end_date":"2026-08-01","limit":20}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"macro.get_credit_conditions","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","observation_date":null,"limit":20}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"macro.regime_snapshot","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","observation_date":null,"include_context":true,"limit":20}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"research.liquidity_credit_state","tool_version":"2.0.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","observation_date":null,"include_context":true,"limit":20}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
~~~
### Current market, energy, and company analytics

Select `market.cross_sectional_performance@2.2.0` for close-basis metadata.
The two energy tools and company fundamentals retain their latest `2.1.0`
contracts.

~~~bash
printf '%s\n' '{"api_version":"1.0","tool":"market.cross_sectional_performance","tool_version":"2.2.0","arguments":{"tickers":["AAPL","MSFT"],"benchmark_ticker":"MSFT","window":20,"mode":"latest","as_of":null,"date_only_policy":"completed_date","limit":100,"start_date":"2026-07-01","end_date":"2026-08-20"}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"energy.get_electricity_retail_sales","tool_version":"2.1.0","arguments":{"metric":"sales","mode":"latest","as_of":null,"date_only_policy":"completed_date","limit":100,"start_date":null,"end_date":null}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"energy.get_weekly_fundamentals","tool_version":"2.1.0","arguments":{"mode":"latest","as_of":null,"date_only_policy":"completed_date","limit":100,"start_date":null,"end_date":null}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
printf '%s\n' '{"api_version":"1.0","tool":"company.get_fundamentals","tool_version":"2.1.0","arguments":{"cik":"0000320193","mode":"latest","as_of":null,"date_only_policy":"completed_date","ratio_codes":["net_margin","liabilities_to_assets"],"limit":1000,"start_date":null,"end_date":null}}' | /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
~~~

The market successor uses explicit tickers and an included benchmark. It
returns cumulative return, breadth counts, annualized sample daily-return
volatility, benchmark-relative cumulative return, and trailing rolling beta.
It has no weights, positions, portfolio, prediction, or imputation semantics.
The energy successors compare each retained observation with the same series
12 monthly or 52 weekly observations earlier. They preserve source-native
units; unavailable history and a zero reference level remain explicit.
The latest company-fundamentals contract computes only `net_margin` and
`liabilities_to_assets` from reviewed normalized SEC facts. It requires
compatible same-duration or same-instant periods and the same base unit; it
does not calculate TTM, calendarized, averaged-balance, or imputed ratios.


The calendar successor pages a retained release-calendar query. Follow only
the opaque `truncation.next_cursor` returned by the immediately preceding
response, keeping every other query field unchanged. The cursor is bound to
the selected filters and availability cutoff and cannot be reused for another
calendar query.

Cross-sectional performance accepts two through fifty explicit retained
tickers. It reports endpoint close-to-close simple return, rank, percentile,
and each ticker's coverage status. It does not infer a universe, construct a
portfolio, apply weights, or imply a position.

Revision analysis compares current retained first and latest official-vintage
values for one reviewed series; it is not a historical as-of replay.
Standardized surprises use the selected retained ex-post population mean and
population standard deviation, so they are also not real-time signals.
Funding and curve tools return source-native observed components and, when
both fields are supplied, one direct left-minus-right same-date spread.
Liquidity, credit, regime, and research-state tools return raw retained
components: liquidity impulse is comparable component end-minus-start change,
the regime is the direct retained NBER indicator, and the research state has
no score, weights, classifier, or investment recommendation.

The energy readers expose only the retained Stage 11 U.S. all-sector
electricity-retail metrics and weekly petroleum-fundamentals history. The
latest company reader exposes normalized reviewed SEC facts for one exact
ten-digit CIK plus only the two ratios described above; it does not calculate
TTM values, calendarized facts, or inferred adjustments. All requests above
are bounded. A valid request
can return no records or a typed not-established outcome when the requested
data is not retained or not available under its declared cutoff.

`start_date` and `end_date` are independently optional for
`market.get_price_series`. Omitting both selects the bounded result available
under the stated mode and limit. The returned typed series are ordered `open`,
`high`, `low`, `close` and preserve provider values. Select `2.0.0`:
a nonempty close series with verified FMP full-EOD capture provenance reports
`adjustment_status: split_adjusted_excluding_distributions` and
`price_adjustment_applied_by_tool: false`. Open, high, low, unbound or empty
close series, and session-calendar semantics remain `not_established`.
See the [binding and metadata contract](#fmp-close-metadata-successors-september-13-2026).
No returned price is rescaled.

When starting from a company name or partial symbol, use
`market.search_instruments@2.0.0` first. It searches retained current Stage 10
FMP identities by a literal substring of provider symbol or display name,
supports an optional exact `asset_type`, and returns opaque cursor pagination
in deterministic symbol order. It is not point-in-time search, and an identity
match does not imply that price rows are available. Confirm the selected
symbol with `market.get_available_ticker` before requesting OHLC data.

`market.get_volume_series` accepts the same ticker, mode, cutoff, optional
inclusive date bounds, date-only policy, and limit shape. It returns one
provider-native volume series selected from the exact same Stage 10 rows. Its
unit is not normalized, and volume-adjustment and session-calendar semantics
are explicitly not established.

`market.technical_indicators` must be selected at its latest version,
`2.8.0`. This additive, store-free contract accepts the new close metadata and includes `supertrend_ai`,
`swing_structure_forecast`, `kdj`, `williams_vix_fix`,
`wavetrend_crosses`, `parabolic_sar`, and
`rolling_regression_line` alongside the base indicator set. Pass its
`series` array the complete typed scalar series taken from the `series` fields
of the price and, when required, volume responses. Do not strip or alter their
metadata, audit, observations, provenance, or lineage digests. Supplying the
full four-series OHLC response is supported even for a close-only calculation.

The latest contract calculates exactly one indicator specification per call.
The required source fields are:

- `close` for every indicator;
- `high`, `low`, and `close` for true range, ATR, Donchian,
  stochastic, ADX, accumulation/distribution, SuperTrend AI,
  Swing Structure Forecast, KDJ, WaveTrend with Crosses, and Parabolic SAR;
- `low` and `close` for Williams Vix Fix; and
- `volume` as well as `close` for OBV, or as well as high/low/close for
  accumulation/distribution.

The `indicator` value includes `sma`, `ema`,
`rolling_standard_deviation`, `rolling_z_score`, `true_range`,
`average_true_range`, `rate_of_change`,
`relative_strength_index`, `macd`, `bollinger_bands`,
`donchian_channels`, `stochastic_oscillator`,
`average_directional_index`, `on_balance_volume`, or
`accumulation_distribution`. Copy all nullable parameter fields from the
selected `describe` result. Parameters unused by the chosen indicator must
be `null`; relevant fields are mandatory. MACD requires
`fast_window < slow_window`; Bollinger requires a positive
`standard_deviation_multiplier` no greater than 10.

Every output retains the input period grid. A warm-up, missing lookback, zero
denominator, or zero price range is an explicit observation with
`value: null` and a `missing_reason`; rows are never silently dropped or
filled. Rolling dispersion and Bollinger use sample standard deviation.
EMA/MACD use an SMA seed, ATR/RSI/ADX use Wilder smoothing, rate of change is
reported on a 0-to-100 percentage scale, flat RSI is defined as 50, OBV starts
at zero, and accumulation/distribution treats a zero high-low range as zero
money-flow volume. These are descriptive transformations, not buy/sell
signals.

### Technical-indicator quick start

Inspect the latest contract before integrating:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.technical_indicators --tool-version 2.8.0
```

The API envelope remains version `1.0`; the selected tool version is
`2.8.0`. Every call must contain `"api_version":"1.0"` and
`"tool_version":"2.8.0"`.

The table below is a navigation aid; the current `describe` result remains
the machine-readable authority. `close` is required in every call, even when
the formula primarily uses high and low. `open` is accepted but no formula
requires it. The `limit` field is also required, must be between 1 and 10,000,
and must be at least the supplied bar count; it is a resource ceiling, not a
request to trim an input. Supply at most five source series. Every nullable
parameter not listed for the chosen indicator must still be present as
`null`.

`ema` is close-only and requires its caller-supplied `window`; use runtime
`describe` for the selected version's schema and limits.

| `indicator` | Required input fields | Non-null parameters | Output components |
| --- | --- | --- | --- |
| `sma` | close | `window` | `sma` |
| `ema` | close | `window` | `ema` |
| `rolling_regression_line` | close | `window` (2 through 10,000) | `rolling_regression_line` |
| `rolling_standard_deviation` | close | `window` (at least 2) | `rolling_standard_deviation` |
| `rolling_z_score` | close | `window` (at least 2) | `rolling_z_score` |
| `true_range` | high, low, close | none | `true_range` |
| `average_true_range` | high, low, close | `window` | `average_true_range` |
| `rate_of_change` | close | `window` | `rate_of_change` |
| `relative_strength_index` | close | `window` | `relative_strength_index` |
| `macd` | close | `fast_window`, `slow_window`, `signal_window`; fast must be below slow | `macd_line`, `signal_line`, `histogram` |
| `bollinger_bands` | close | `window` (at least 2), `standard_deviation_multiplier` (greater than 0 and at most 10) | `middle_band`, `upper_band`, `lower_band` |
| `donchian_channels` | high, low, close | `window` | `upper_channel`, `middle_channel`, `lower_channel` |
| `stochastic_oscillator` | high, low, close | `window`, `signal_window` | `percent_k`, `percent_d` |
| `average_directional_index` | high, low, close | `window` | `plus_di`, `minus_di`, `adx` |
| `on_balance_volume` | close, volume | none | `on_balance_volume` |
| `accumulation_distribution` | high, low, close, volume | none | `accumulation_distribution` |

#### SuperTrend AI

The latest contract includes this calculation:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.technical_indicators --tool-version 2.8.0
```

`supertrend_ai` requires high, low, and close plus these non-null fields:

| Field | Pine-equivalent default | Contract |
| --- | ---: | --- |
| `window` | 10 | ATR length, integer from 1 through 10,000 |
| `minimum_factor` | 1 | finite number from 0 through 100 |
| `maximum_factor` | 5 | finite number from the minimum through 100 |
| `factor_step` | 0.5 | finite number greater than 0 and at most 100 |
| `performance_memory` | 10 | finite number from 2 through 10,000 |
| `cluster` | `best` | exactly `best`, `average`, or `worst` |

The factor grid must contain 3 through 101 candidates. Set `fast_window`,
`slow_window`, `signal_window`, and `standard_deviation_multiplier` to `null`.
The five output components are `trailing_stop`,
`adaptive_moving_average`, `trend`, `performance_index`, and `target_factor`.
`trend` is the numeric regime state 0 or 1; it is not a buy/sell event.

The conversion deliberately uses the request's causal input range. The
required `limit` replaces Pine's last-bar-relative `maxData`; it must cover the
supplied observations, and the host rejects an excessive bars-by-factor-grid
workload. Pine's default inclusive `0..1000` clustering loop is fixed as a
1,001-assignment cap. Empty clusters retain their prior centroids. Colors,
candle gradients, labels, signals, tables, and dashboard settings are not part
of the analytical result.

#### Swing Structure Forecast

The latest contract includes this calculation:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.technical_indicators --tool-version 2.8.0
```

`swing_structure_forecast` requires high, low, and close plus these non-null
fields:

| Field | Pine-equivalent default | Contract |
| --- | ---: | --- |
| `window` | 16 | swing length, integer from 10 through 5,000 |
| `sample_count` | 20 | newest completed swing legs retained, integer from 3 through 20 |
| `aggregation_method` | `weighted` | exactly `weighted`, `average`, or `median` |

Set `fast_window`, `slow_window`, `signal_window`,
`standard_deviation_multiplier`, `minimum_factor`, `maximum_factor`,
`factor_step`, `performance_memory`, and `cluster` to `null`. The ten output
components, in order, are `confirmed_swing_high`, `confirmed_swing_low`,
`swing_direction`, `forecast_origin`, `forecast_target`,
`forecast_percent`, `forecast_duration_bars`,
`forecast_standard_deviation`, `forecast_band_half`, and
`forecast_origin_age_bars`. Direction is `1` for bullish and `-1` for bearish
after warm-up; duration and age are bar counts.

The conversion preserves one-bar pivot confirmation, the source's low-test
win when the current bar is both rolling extremes, newest-sample retention,
population forecast dispersion, and fixed Wilder ATR(200) as the minimum band
width. The underlying calculation resets state on a missing value, while the
public call inherits v2's stricter rule and rejects any missing input
observation before calculation. Because Pine draws
its forecast only on the latest chart bar, the tool causally unrolls the
calculation: every output row is the latest-bar result for exactly that input
prefix. It does not invent a future trading date. Forecast-bar placement,
beams, target boxes, dots, Fibonacci drawings, support/resistance objects,
alerts, colors, and labels are excluded presentation behavior.

#### KDJ

The latest contract includes this calculation:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.technical_indicators --tool-version 2.8.0
```

`kdj` requires high, low, and close plus these non-null fields:

| Field | Pine-equivalent default | Contract |
| --- | ---: | --- |
| `window` | 9 | Pine `ilong`: rolling high/low period, integer from 1 through 10,000 |
| `signal_window` | 3 | Pine `isig`: BCWSMA length for K and D, integer from 1 through 10,000 |

Set every other indicator field to `null`. The outputs are `percent_k`,
`percent_d`, and `percent_j`; before the complete `window`, all three report
`insufficient_history`. RSV uses the complete rolling high/low window. K and
D use the Pine BCWSMA recurrence with weight 1 and an `nz` previous value of
zero; J is `3 * K - 2 * D` and is not clamped. A flat high/low window reports
`zero_price_range`; at the next valid RSV, both recurrences restart from zero.
Public calls reject source observations containing null or missing values.
Plot colors, background shading, and the 20/80 guide lines are presentation
behavior and are excluded.

#### Williams Vix Fix

The latest contract includes this calculation:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.technical_indicators --tool-version 2.8.0
```

`williams_vix_fix` requires low and close plus these non-null fields. The
contract has no implicit parameter defaults; the values below are a common
Pine-compatible request and must be sent explicitly.

| Field | Example value | Contract |
| --- | ---: | --- |
| `window` | 22 | highest-close lookback, integer from 1 through 10,000 |
| `signal_window` | 20 | Bollinger lookback, integer from 1 through 10,000 |
| `standard_deviation_multiplier` | 2 | finite number from 1 through 5 |
| `percentile_window` | 50 | percentile lookback, integer from 1 through 10,000 |
| `percentile_high_factor` | 0.85 | finite number greater than 0 and at most 1 |
| `percentile_low_factor` | 1.01 | finite number from 1 through 10 |

Set every other indicator parameter field to `null`. The four outputs are
`williams_vix_fix`, `upper_band`, `range_high`, and `range_low`. The
Bollinger threshold uses population standard deviation, and every threshold
uses a full rolling window. A zero highest close is explicit missingness.
Display toggles, colors, and plot styles are excluded presentation behavior.

#### WaveTrend with Crosses

The latest contract includes this calculation:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.technical_indicators --tool-version 2.8.0
```

`wavetrend_crosses` requires high, low, and close plus these non-null fields:

| Field | Pine-equivalent default | Contract |
| --- | ---: | --- |
| `window` | 10 | channel length, integer from 1 through 10,000 |
| `signal_window` | 21 | average length, integer from 1 through 10,000 |

Set every other indicator parameter field to `null`. The four outputs are
`wavetrend`, `wavetrend_signal`, `wavetrend_difference`, and
`wavetrend_cross_signal`. The calculation uses HLC3, SMA-seeded EMAs, fixed
`0.015` channel scaling, and the source's fixed four-bar SMA signal. The cross
component is `1` for a bullish cross, `-1` for a bearish cross, and `0`
otherwise. A zero smoothed channel deviation is explicit
`zero_channel_deviation` missingness. Overbought and oversold levels, plot
colors, area fill, cross circles, and bar colors are presentation behavior and
are excluded.

#### Parabolic SAR

The latest contract includes this calculation:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.technical_indicators --tool-version 2.8.0
```

`parabolic_sar` requires high, low, and close plus finite `start`,
`increment`, and `maximum` numbers. The contract has no implicit parameter
defaults; a conventional Pine-compatible request sends `0.02`, `0.02`, and
`0.2`, respectively. Set every other indicator parameter field to `null`.

The single output is `parabolic_sar`. The first bar has explicit
`insufficient_sar_history` missingness. The causal recurrence initializes
trend from consecutive closes, increments acceleration only at new extremes,
resets acceleration and the extreme on reversal, and clamps the stop against
the prior two lows or highs. Missing OHLC resets recursive state. Plot crosses,
colors, timeframe controls, and chart-gap presentation are excluded.


#### Rolling regression line

Inspect the latest contract before constructing a request; runtime `describe`
is the schema authority:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe market.technical_indicators --tool-version 2.8.0
```

`rolling_regression_line` requires close plus an explicit integer `window`
from 2 through 10,000; every other nullable indicator parameter must be
`null`. At each bar it fits ordinary least squares over exactly the trailing
complete window including the current close, with x values 0 through
`window - 1`, and emits the fitted value at x = `window - 1`.

The output is a single aligned `rolling_regression_line` price series. It is
causal and retains the full input grid: warm-up rows report
`insufficient_history`; any null close inside the trailing window reports
`rolling_window_input_missing`; and valid output resumes immediately when
that gap leaves the trailing window.


This latest-contract standard-library example is suitable for an agent running
from another local project. It reads a bounded, non-truncated SPY window and
passes the complete typed OHLC series to Bollinger Bands without reconstructing
or editing any series metadata:

```python
import json
import subprocess

TOOLS = "/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools"


def call(tool, arguments, *, tool_version):
    envelope = {
        "api_version": "1.0",
        "tool": tool,
        "tool_version": tool_version,
        "arguments": arguments,
    }
    completed = subprocess.run(
        [TOOLS, "call"],
        input=json.dumps(envelope, allow_nan=False, separators=(",", ":")),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    response = json.loads(completed.stdout)
    if completed.returncode != 0:
        error = response.get("error", {})
        raise RuntimeError(
            f"{error.get('code', 'tool_error')}: "
            f"{error.get('message', 'local tool call failed')}"
        )
    return response


selection = {
    "ticker": "SPY",
    "start_date": "2025-01-01",
    "end_date": "2025-12-31",
    "mode": "latest",
    "as_of": None,
    "date_only_policy": "completed_date",
    "limit": 10_000,
}
price_response = call(
    "market.get_price_series",
    selection,
    tool_version="2.0.0",
)
price_result = price_response["result"]
if price_result["truncation"]["applied"]:
    raise RuntimeError("Choose a smaller date range; indicator inputs cannot be truncated")

indicator_response = call(
    "market.technical_indicators",
    {
        "series": price_result["series"],
        "indicator": "bollinger_bands",
        "window": 20,
        "fast_window": None,
        "slow_window": None,
        "signal_window": None,
        "standard_deviation_multiplier": 2,
        "minimum_factor": None,
        "maximum_factor": None,
        "factor_step": None,
        "performance_memory": None,
        "cluster": None,
        "sample_count": None,
        "aggregation_method": None,
        "percentile_window": None,
        "percentile_high_factor": None,
        "percentile_low_factor": None,
        "start": None,
        "increment": None,
        "maximum": None,
        "limit": 10_000,
    },
    tool_version="2.8.0",
)

for output in indicator_response["result"]["series"]:
    print(output["metadata"]["component"], len(output["observations"]))
```

Using the same `call`, `price_result`, and truncation check, the Pine-equivalent
default SuperTrend AI calculation is:

```python
supertrend_response = call(
    "market.technical_indicators",
    {
        "series": price_result["series"],
        "indicator": "supertrend_ai",
        "window": 10,
        "fast_window": None,
        "slow_window": None,
        "signal_window": None,
        "standard_deviation_multiplier": None,
        "minimum_factor": 1,
        "maximum_factor": 5,
        "factor_step": 0.5,
        "performance_memory": 10,
        "cluster": "best",
        "sample_count": None,
        "aggregation_method": None,
        "percentile_window": None,
        "percentile_high_factor": None,
        "percentile_low_factor": None,
        "start": None,
        "increment": None,
        "maximum": None,
        "limit": 10_000,
    },
    tool_version="2.8.0",
)

for output in supertrend_response["result"]["series"]:
    print(output["metadata"]["component"], len(output["observations"]))
```

The Pine-equivalent default Swing Structure Forecast calculation is:

```python
swing_response = call(
    "market.technical_indicators",
    {
        "series": price_result["series"],
        "indicator": "swing_structure_forecast",
        "window": 16,
        "fast_window": None,
        "slow_window": None,
        "signal_window": None,
        "standard_deviation_multiplier": None,
        "minimum_factor": None,
        "maximum_factor": None,
        "factor_step": None,
        "performance_memory": None,
        "cluster": None,
        "sample_count": 20,
        "aggregation_method": "weighted",
        "percentile_window": None,
        "percentile_high_factor": None,
        "percentile_low_factor": None,
        "start": None,
        "increment": None,
        "maximum": None,
        "limit": 10_000,
    },
    tool_version="2.8.0",
)

for output in swing_response["result"]["series"]:
    print(output["metadata"]["component"], len(output["observations"]))
```

The Pine-equivalent default KDJ calculation is:

```python
kdj_response = call(
    "market.technical_indicators",
    {
        "series": price_result["series"],
        "indicator": "kdj",
        "window": 9,
        "fast_window": None,
        "slow_window": None,
        "signal_window": 3,
        "standard_deviation_multiplier": None,
        "minimum_factor": None,
        "maximum_factor": None,
        "factor_step": None,
        "performance_memory": None,
        "cluster": None,
        "sample_count": None,
        "aggregation_method": None,
        "percentile_window": None,
        "percentile_high_factor": None,
        "percentile_low_factor": None,
        "start": None,
        "increment": None,
        "maximum": None,
        "limit": 10_000,
    },
    tool_version="2.8.0",
)

for output in kdj_response["result"]["series"]:
    print(output["metadata"]["component"], len(output["observations"]))
```

Using the same unmodified `price_result`, a conventional Parabolic SAR
request is:

```python
psar_response = call(
    "market.technical_indicators",
    {
        "series": price_result["series"],
        "indicator": "parabolic_sar",
        "window": None,
        "fast_window": None,
        "slow_window": None,
        "signal_window": None,
        "standard_deviation_multiplier": None,
        "minimum_factor": None,
        "maximum_factor": None,
        "factor_step": None,
        "performance_memory": None,
        "cluster": None,
        "sample_count": None,
        "aggregation_method": None,
        "percentile_window": None,
        "percentile_high_factor": None,
        "percentile_low_factor": None,
        "start": 0.02,
        "increment": 0.02,
        "maximum": 0.2,
        "limit": 10_000,
    },
    tool_version="2.8.0",
)

for output in psar_response["result"]["series"]:
    print(output["metadata"]["component"], len(output["observations"]))
```



For OBV or accumulation/distribution, call `market.get_volume_series` with
the exact same `selection`, reject a truncated result, and concatenate its
unchanged `result.series` array to `price_result["series"]`. Preserve both
source receipts with the indicator receipt. If any source selection differs
in instrument, dates, mode, cutoff, capture identity, or period grid, the
indicator call correctly fails closed. Do not trim, merge, or synthesize
source metadata, audits, observations, provenance, or lineage.

A successful latest technical-indicator call returns the wrapper fields
`api_version`, `execution`, `tool`, `result`, and `receipt`. The result is a
`QueryResultV1` with the selected indicator's aligned component series in the
documented order, one `technical_indicator_component` record per series,
diagnostics, warnings, lineage, and `truncation.applied: false`. Each derived
series identifies its indicator, component, parameters, and source fields in
metadata. Its
observations retain the complete input grid; warm-up and undefined points use
`value: null` plus `missing_reason`. Keep the full wrapper and receipt with
downstream work rather than retaining only the numeric values.

The latest canonical macro interface is version `2.0.0`. Start with
`macro.search_series`, then `macro.describe_series`, and pass the returned
exact `series_id` to `macro.get_series`, explicitly passing `2.0.0` to
each call. The reader supports `latest`, `as_of`, and
`first_release`; `as_of` uses stored availability rather than period dates,
and `first_release` fails closed unless the selected storage model retains an
explicit flag and evidence. Official-vintage IDs never fall back to a generic
series with the same ID. Preserve nullable source-native vintage fields in
the returned `MacroTimeSeriesV2` rather than inventing timestamps.

`macro.get_release_calendar` supports `latest` and `as_of`, optional inclusive
event-date bounds, an optional case-insensitive event-name substring, and explicit
truncation. Its availability is retained local capture time. It does not
support or imply first-release selection.

The Step 2-4 analytical tools do not open a database. Pass them the complete
typed trailing-return series returned by the explicitly selected
`market.get_returns@2.1.0` contract, preserving its audit, lineage, return
definition, and point-in-time metadata exactly. Select the latest `2.1.0`
contracts explicitly for `data.quality_audit` and `timeseries.transform`.
The four base `stats.*` tools use their latest `2.0.0` contracts.

Use `data.quality_audit@2.1.0` before inference when coverage, explicit
missingness, duplicates, ordering, availability, truncation, or lineage needs
to be checked. Its calendar gaps do not establish missing exchange sessions.
Use `timeseries.transform@2.1.0` for one explicitly selected rolling, ACF,
PACF, Ljung-Box, or drawdown operation; copy its operation-specific nullable
fields exactly from `describe` because irrelevant parameters are rejected.

The general statistics tools operate on compatible, non-truncated returns.
Covariance and PCA use one outer-aligned, joint-complete sample rather than
pairwise-changing samples. Bootstrap requires an explicit unsigned 32-bit
`seed`, and its result echoes the seed and deterministic generator contract.
PCA requires an explicit covariance or correlation `basis`, orders components
by descending eigenvalue with deterministic ties, canonicalizes eigenvector
signs, and includes scores only when `include_scores` is true. Treat typed
not-established results for insufficient samples, zero variance, interior
missingness, or rank limitations as outcomes rather than silently changing
the sample.

Always include the latest semantic version in the envelope:

```json
{
  "api_version": "1.0",
  "tool": "market.get_returns",
  "tool_version": "2.1.0",
  "arguments": {}
}
```

Always copy `arguments` and version values from that tool's current `describe`
result. The illustrative empty arguments above are not a valid request unless
the selected schema says so.

The parser rejects duplicate object keys, malformed UTF-8/JSON, `NaN` and
infinite numbers, extra envelope fields, unknown tools, unknown versions, and
caller-supplied paths, SQL, PRAGMAs, credentials, providers, or imports. A
successful command exits `0`; client/request failures exit `2`; unavailable
stores or internal failures exit `1`. Errors are also a sanitized JSON object
on stdout, so agents should parse stdout regardless of the exit status.

## Private manual macro collectors

The following credential-free, live collector CLIs are private and
`manual_only`. They are deliberately outside the read-only
`bin/quant-data-tools` interface, which must never be used to fetch or write
provider data. This documentation does not confer provider-execution,
canonical-store-write, scheduler, retry, or timer authority: run one only when
the current user request explicitly authorizes its finite scope.

The manual aggregate wrapper is:

```bash
python3 -m quant_data.operations.macro_database_expansion_refresh --as-of YYYY-MM-DD --series-break SBNYYYY
```

Its fixed workload is serial, one attempt with no retry, capped at 63 provider
requests, and targets only the fixed canonical macro store
`data/macro.sqlite`. It accepts an explicit NY Fed primary-dealer series break
in the form `SBNYYYY`; it does not accept a caller-selected store path.

The six existing individual forms are:

```bash
# CFTC TFF futures-only: at most 21 inclusive days and 8 requests.
python3 -m quant_data.operations.cftc_cot_history --report-family tff_futures_only --from YYYY-MM-DD --to YYYY-MM-DD

# CFTC disaggregated futures-only: at most 21 inclusive days and 8 requests.
python3 -m quant_data.operations.cftc_cot_history --report-family disaggregated_futures_only --from YYYY-MM-DD --to YYYY-MM-DD

# Treasury securities auctions: at most 366 inclusive days and 1 request.
python3 -m quant_data.operations.treasury_securities_auctions_history --from YYYY-MM-DD --to YYYY-MM-DD

# NY Fed Primary Dealer Statistics: at most 5,000 inclusive days, an explicit
# SBNYYYY series break, and 33 requests.
python3 -m quant_data.operations.nyfed_primary_dealer_statistics_history --series-break SBNYYYY --from YYYY-MM-DD --to YYYY-MM-DD

# Federal Reserve H.8: at most 5,000 inclusive days and 7 singleton requests.
python3 -m quant_data.operations.federal_reserve_credit_conditions_history --source-key federal_reserve_h8 --from YYYY-MM-DD --to YYYY-MM-DD

# Federal Reserve SLOOS: at most 5,000 inclusive days and 6 singleton requests.
python3 -m quant_data.operations.federal_reserve_credit_conditions_history --source-key federal_reserve_sloos --from YYYY-MM-DD --to YYYY-MM-DD
```

The active `quant-data-macro-current-refresh.timer` now runs 27 operations
with an 81-request cap. Its four live-validated additions are both CFTC
futures-only families, Treasury securities auctions, and NY Fed Primary Dealer
Statistics using fixed `SBN2024`. H.8 and SLOOS remain manual-only and are not
called by the timer: on 2026-09-04, all 13 authorized singleton attempts from
this host ended before an HTTP response, with zero retries and zero accepted
bodies. After an authorized run, query retained results only through the
existing read-only `macro.search_series`, `macro.describe_series`,
`macro.get_series`, and `data.get_dataset_status` tools (or the Inspector's
read-only `/data-status` view), never by opening the macro store directly.

## Read-only data boundary

Do not read `data/*.sqlite` directly, open it through SQLite, attach it to an
analytics engine, or copy its path into another project. The CLI fixes the
project root, canonical registry, and four logical store routes itself. Each
registered operation uses a parameterized read-only gateway and can open only
the store(s) declared for that tool. It cannot initialise, migrate, repair, or
write a store.

Retained-data tools make no provider requests or credential reads. The explicit
`price_realtime` invocation is the live FMP quote exception described above;
it cannot write stores. A tool that cannot establish a result from its available
evidence may return a typed
`not_established` result rather than invented data; treat that as an analytical
outcome, not an invitation to bypass the boundary.

## Interpreting results safely

Every successful `call` response includes `result` and a sanitized `receipt`.
Keep the receipt with downstream work. It records the selected tool and
semantic version, registry revision, operation graph, logical stores,
validated bounds, shape, warnings, truncation, and available analysis/lineage
identifiers without exposing local paths or source payloads.

- `warnings` and `diagnostics` explain limitations, exclusions, missingness,
  and not-established conditions.
- `truncation` distinguishes a complete result from a bounded partial one. Do
  not pass truncated series into analysis that requires a complete sample.
- `lineage` and time-series audit fields preserve source and transformation
  provenance; retain them when handing data to another analysis.
- `mode: "latest"` selects the latest eligible evidence. When a tool supports
  `as_of`, pass a declared cutoff and retain its point-in-time metadata. Never
  infer that an ex-post result is point-in-time safe.

## Typical composition

Use tools as a sequence of validated typed results:

1. If starting from a name or partial symbol, search retained identities with
   `market.search_instruments@2.0.0`.
2. Confirm retrievable price coverage with `market.get_available_ticker`.
3. Retrieve unchanged provider OHLC with `market.get_price_series@2.0.0`
   for one returned ticker; inspect the close-specific basis metadata.
4. Retrieve provider-native volume with `market.get_volume_series` when the
   analysis needs it.
5. For descriptive technical analysis, pass those unmodified typed series to
   `market.technical_indicators@2.8.0`, one indicator specification per call.
6. Use `market.get_returns@2.1.0` or `market.get_forward_returns@2.1.0`
   to produce compatible price-return series.
7. Run `data.quality_audit@2.1.0` and resolve or retain every reported quality
   limitation before inference.
8. Feed compatible, non-truncated series into the explicitly selected
   transformation, general-statistics, or econometrics contract.
9. Preserve each response's receipt, lineage, point-in-time policy, and
   warnings with the final research artifact.

For macro work, search and describe with the latest `2.0.0` contracts first,
retrieve the series under a declared `latest`, `as_of`, or evidenced
`first_release` mode,
then align only frequency-compatible, non-truncated outputs. Use the calendar
tool separately when release timing is part of the design; do not infer
release timing from observation periods.

Do not silently fill missing observations, mix incompatible adjustment bases,
change the return definition, or discard point-in-time metadata
between those steps.


## ETF allocator snapshot

Use `portfolio.get_etf_allocator_snapshot@2.0.0` for current research.
The tool accepts 1-25 unique exact symbols from its fixed roster and returns
the requested subset in request order. Request the complete roster for the
25-ETF workflow; SGOV is excluded. Inspect the exact contract first:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe portfolio.get_etf_allocator_snapshot --tool-version 2.0.0
```

This standalone WSL/Linux Python example captures the actual current UTC
decision time immediately before the call. It reads retained data; it does
not refresh prices. Preserve the complete printed response as the receipt
for that decision.

```python
from datetime import datetime, timezone
import json
import subprocess

launcher = "/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools"
request = {
    "api_version": "1.0",
    "tool": "portfolio.get_etf_allocator_snapshot",
    "tool_version": "2.0.0",
    "arguments": {
        "symbols": [
            "SPY", "QQQ", "DIA", "IWM", "VEA", "VWO", "IEF", "TLT", "TIP",
            "LQD", "HYG", "GLD", "SLV", "PDBC", "XLB", "XLC", "XLE",
            "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
        ],
        "decision_as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    },
}
completed = subprocess.run(
    [launcher, "call"],
    input=json.dumps(request, allow_nan=False),
    text=True, capture_output=True, check=False,
)
response = json.loads(completed.stdout)
print(json.dumps(response, indent=2, allow_nan=False))
raise SystemExit(completed.returncode)
```

`decision_as_of` is required, offset-aware, and cannot exceed the execution
clock. The fixed market reader uses one descriptor-pinned immutable
transaction for the entire request, filters retained versions and identities
by local availability, and checks main/WAL/SHM/journal stamps. It opens no
other store and performs no provider request.

The public result uses the existing `QueryResultV1` shape: one
`etf_allocator_features` record per requested symbol and one
`etf_allocator_snapshot` diagnostic containing snapshot metadata. Convert
each record's `fields` and the diagnostic's `metrics` arrays from
`{name,value}` pairs to mappings. List-valued diagnostic fields such as
`missing_inputs`, `missing_session_dates`, `missing_month_end_dates`,
`limitations`, and `lineage_sha256` are strict JSON strings, following
the platform's scalar-field convention. Retain the complete outer receipt.

### Calculation conventions and evidence requirements

- Price basis: split-adjusted prices **excluding distributions**, in the
  source price unit. Neither dividend-adjusted nor total-return prices qualify.
  Version 2 binds retained FMP full-EOD `close` to this convention when its
  capture provenance is supported, and calculates each feature independently.
  It applies no further split factor. The pure calculation kernel is tested
  on explicitly split-only inputs. The version 1 gate is preserved only for
  [historical compatibility](#retained-data-request-on-2026-09-05).
- Calendar: the fixed US cash-equity regular-session schedule for
  `2024-01-01` through `2026-12-31`, in `America/New_York`.
  [NYSE's published 2024–2026 calendar](https://ir.theice.com/press/news-details/2023/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar/default.aspx)
  supplies holidays and 13:00 early closes; normal close is 16:00.
  The [January 9, 2025 closure](https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx)
  is included. Calendar coverage is explicit and bounded, without a new
  dependency or runtime calendar fetch.
- Endpoint: let `m` be the latest month whose final scheduled session has
  closed at or before the decision instant. Use that session's exact close.
  An early or missing observation cannot replace it. For example, March 2024
  ends March 28 after its close; the Good Friday holiday is March 29.
  `selected_month_end` and `final_eligible_month_end` describe calendar
  eligibility, not data readiness. Missing prices do not move the shared
  endpoint backward. Actual available price dates are reported per symbol;
  `feature_observation_date` remains null when no feature is established.
- SMA: arithmetic mean of ten exact monthly closes, `P(m-9)..P(m)`.
- Returns: `return_12_to_1_month = P(m-1)/P(m-12)-1`.
  The other three returns are `P(m)/P(m-k)-1`, for `k=3,6,12`.
  They require the exact contributing endpoints; absent intermediate daily
  rows do not change an endpoint return. The longest lookback spans thirteen
  monthly endpoints including the current endpoint.
- Volatility: 21, 63, or 252 consecutive regular-session simple returns
  `r(t)=P(t)/P(t-1)-1`, sample standard deviation with denominator
  `n-1`, multiplied by `sqrt(252)`. Units are annualized fractions
  (`0.20` means 20%). Each requires `n+1` positive finite closes on
  the expected session grid. Interior missing sessions are not bridged.
  Zero measured variance remains zero; it is never substituted for missing data.
- Decimal calculations use 34-digit local precision. Missing, nonpositive,
  nonfinite, insufficient-history, out-of-calendar, and unsupported-basis
  inputs produce explicit reasons. All requested rows are returned; there is
  no caller-selected limit or silent truncation.
- The as-of audit is limited to retained local captures. It does not establish
  original provider publication time, historical listing/classification state,
  or a split/distribution adjustment history available at the decision.
  `point_in_time_status` remains `not_established`, including when price
  dates are old. A pre-retention cutoff cannot borrow current prices.

Return all three volatility measures when their evidence is available.
The consuming app chooses its weighting-volatility rule. Quant emits no
approved volatility, qualification, structure approval, portfolio cap,
cluster selection, or risk scale.

Classification metadata uses `retained_identity_only` when the retained symbol,
stable ID and asset type are available. Exposure, legal structure and
leveraged/inverse fields remain null. Missing identities use `not_retained`.
A retained non-ETF identity fails closed with `instrument_type_mismatch`
before price selection.

### ETF version 2: provider-adjusted close

The September 13 user decision accepts FMP's documented full-EOD `close`
as the strategy's split-adjusted, dividend-excluding input. Registry
`2.84.0` / catalog `2.30.0` adds
`portfolio.get_etf_allocator_snapshot@2.0.0`; version 1 and the unversioned
default remain unchanged. The input arguments, roster, monthly endpoint,
calendar, nine formulas, immutable reader and receipts remain the same.

FMP's [official FAQ](https://site.financialmodelingprep.com/faqs) distinguishes
split-only `close` from `adjClose`, which includes split and dividend
adjustments. The [full-EOD endpoint](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-full)
is `/stable/historical-price-eod/full`. Our parser preserves `close` as
`close_value` under `stage10.fmp.daily_price.v1`; Stage 10 history and
the existing incremental publishers use that same physical normalization.

Version 2 verifies the selected captures' provider, instrument, endpoint and
normalization version, together with the selected price variant. It binds
only `fmp_full_eod_v1` / `provider_native` prices from the known parser.
An unsupported endpoint or parser leaves the basis unestablished. There is
no caller option to assert provenance or substitute another field.

**Use close unchanged.** Neither ingestion nor the snapshot needs a second
split adjustment, a split-history join, a dividend reversal, or an
`adjClose` substitution. The snapshot makes no provider requests and
does not modify retained prices. Raw and dividend-adjusted prices do not
qualify under this binding.

Each record adds `source_price_field: close`,
`price_adjustment_applied_by_tool: false`, the binding ID
`fmp_full_eod_close_split_only_v1` when supported, a documentation URL,
`source_capture_count`, `adjustment_vintage_status`,
`source_publication_time: null`, and `available_feature_count`.
Each feature is calculated independently from its required prices.
`data_quality_status` is `ready` for all nine values, `partial` for
some values, and `blocked` for zero available values. Missing dates and per-feature reasons
remain explicit; historical reconstruction is not a numeric feature gate.

Snapshot `complete` and `ready_symbol_count` refer only to the nine
price features. The summary separates `source_adjustment_binding_required`
from observed binding and reports `bound_source_symbol_count`; its observed
binding is null unless every requested symbol has bound source prices. Overall result `status` is `ok` only when all requested
symbols have all nine; partial numeric results use `not_established`
with per-record readiness. This is not instrument approval, execution
authority or backtest certification. Classification completeness remains
separate and leverage/inverse/legal-structure fields remain null where absent.

The explicit research convention is
`retained_provider_adjusted_close_at_each_capture`. The observation date
is the price's trading date; `source_available_from/through` are actual
local capture bounds. Original publication times remain unknown.
`feature_observation_date` is the selected month-end anchoring the feature
packet when any feature is available; it does not imply an endpoint price
exists for a feature such as 12-to-1 momentum that excludes that endpoint.
`source_last_observation_date` still covers the feature window only,
not the latest retained price anywhere in the database.

Provider split adjustments are delivered at each capture. Multiple captures
are labeled `mixed_provider_captures` with a warning; they do not certify
a common adjustment vintage. A complete lookback from one provider response
can supply one vintage without maintaining local split history. Such a
refresh uses the existing collector and publisher and requires its own finite
fetch scope. Version 2 does not silently reconstruct or rescale old captures.

`point_in_time_status` remains `not_established` for historical
reconstruction, even with complete numerical features. All identities and
prices must still have been captured by `decision_as_of`; no later capture
may be backdated to its observation date.

Consumers should change only the selected `tool_version` to `2.0.0`,
retain the standard receipt, read the per-feature values/reasons and
readiness metadata, and preserve adjustment-vintage and historical warnings.
Select allocation volatility, risk limits and approval policy in the consumer;
version 2 removes the legacy volatility recommendation from the summary.

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe portfolio.get_etf_allocator_snapshot --tool-version 2.0.0
```

The [complete-roster example](#etf-allocator-snapshot) already selects this
version and uses the actual current UTC decision instant.

The [September 13 verification](rebuild/ETF_ALLOCATOR_INVESTIGATION_2026-09-13.md#version-2-public-verification-at-041201-utc)
returned all 25 symbols and 33 values: all nine features for SPY and
12-to-1-month momentum for each other ETF. At that capture, the remaining August
price gaps prevented a complete 25-symbol feature packet. This dated result
does not promise the same coverage at a later decision time.

### Retained-data request on 2026-09-05

**Historical version 1 audit only. Do not copy this request for current work.**
Registry `2.70.0` added `portfolio.get_etf_allocator_snapshot@1.0.0`.
At that release the manifest contained 75 logical tools and catalog `2.27.0`
contained 158 contracts. The 57 recovered defaults and exact `2.69.0`
predecessor are preserved. Version 1 had no binding to FMP's documented field
semantics and withheld all nine features with explicit missing reasons.
It did not apply an inferred split factor.

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe portfolio.get_etf_allocator_snapshot --tool-version 1.0.0

/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call <<'JSON'
{"api_version":"1.0","tool":"portfolio.get_etf_allocator_snapshot","tool_version":"1.0.0","arguments":{"symbols":["SPY","QQQ","DIA","IWM","VEA","VWO","IEF","TLT","TIP","LQD","HYG","GLD","SLV","PDBC","XLB","XLC","XLE","XLF","XLI","XLK","XLP","XLRE","XLU","XLV","XLY"],"decision_as_of":"2026-09-05T00:00:00Z"}}
JSON
```

The exact public invocation above exited 0, returned all 25 identities, selected
calendar endpoint `2026-08-31`, and reported `status: not_established`,
`complete: false`, `truncated: false`, and zero feature-ready symbols.
The immutable reader and explicit before/after check found unchanged market
and sidecar stamps. No ingestion occurred.

At that capture, every symbol lacked established split-only adjustment provenance, historical
adjustment reconstruction, the August 31 endpoint, and supported exchange,
exposure, legal-structure, leveraged/inverse metadata. These metadata fields
are null rather than inferred from the ticker or the word ETF.

| Symbol | Retained rows in requested lookback | Last retained price | Missing sessions through Aug 31 | Feature readiness |
| --- | ---: | --- | ---: | --- |
| SPY | 261 | 2026-08-14 | 11 | Blocked |
| QQQ | 261 | 2026-08-14 | 11 | Blocked |
| DIA | 261 | 2026-08-14 | 11 | Blocked |
| IWM | 270 | 2026-08-27 | 2 | Blocked |
| VEA | 261 | 2026-08-14 | 11 | Blocked |
| VWO | 261 | 2026-08-14 | 11 | Blocked |
| IEF | 261 | 2026-08-14 | 11 | Blocked |
| TLT | 261 | 2026-08-14 | 11 | Blocked |
| TIP | 261 | 2026-08-14 | 11 | Blocked |
| LQD | 261 | 2026-08-14 | 11 | Blocked |
| HYG | 261 | 2026-08-14 | 11 | Blocked |
| GLD | 261 | 2026-08-14 | 11 | Blocked |
| SLV | 261 | 2026-08-14 | 11 | Blocked |
| PDBC | 261 | 2026-08-14 | 11 | Blocked |
| XLB | 261 | 2026-08-14 | 11 | Blocked |
| XLC | 261 | 2026-08-14 | 11 | Blocked |
| XLE | 261 | 2026-08-14 | 11 | Blocked |
| XLF | 261 | 2026-08-14 | 11 | Blocked |
| XLI | 261 | 2026-08-14 | 11 | Blocked |
| XLK | 261 | 2026-08-14 | 11 | Blocked |
| XLP | 261 | 2026-08-14 | 11 | Blocked |
| XLRE | 261 | 2026-08-14 | 11 | Blocked |
| XLU | 261 | 2026-08-14 | 11 | Blocked |
| XLV | 261 | 2026-08-14 | 11 | Blocked |
| XLY | 261 | 2026-08-14 | 11 | Blocked |

All rows start at `2025-08-01` within this request's bounded lookback.
The non-IWM gaps are August 17–21, 24–28, and 31; IWM lacks August 28 and 31.
These are retained-data findings, not authorization to fetch or repeat a
historical population. At that stage, resolving readiness required a separately
scoped data decision and establishing split-only source semantics and provenance.
The later version 2 binding does not rewrite this version 1 evidence.

A sanitized excerpt of the actual response (other rows, metrics, and receipt
fields omitted only in this documentation) is:

```json
{"result":{"status":"not_established","records":[{"record_type":"etf_allocator_features","fields":[{"name":"symbol","value":"SPY"},{"name":"instrument_id","value":"stage10_instrument_3e453d444b94440afdf0f3a84d2d6a7b"},{"name":"split_adjusted_price","value":null},{"name":"split_adjusted_price_missing_reason","value":"adjustment_basis_not_established"},{"name":"source_last_observation_date","value":"2026-08-14"}]}],"truncation":{"applied":false,"limit":25,"returned_count":25,"total_known_count":25,"has_more":false,"next_cursor":null}}}
```

### Minimal investment-app adjustment

**Historical September 5 version 1 handoff.** The instructions below describe
that integration and its blocked response, not the current version to select.
Use the [version 2 consumer guidance](#etf-version-2-provider-adjusted-close)
for current work. Consumer policy details here record the old interface review
and do not assign portfolio decisions to Quant.

The five requested consumer files were reviewed as interface references only.
This Quant integration did not modify the investment-app project. The original
handoff suggested `realized_volatility_63` as one app-side choice; version 2
removes that recommendation and leaves the selection entirely to the app.

1. Call the explicit advertised `1.0.0` version and adapt the standard
   records/diagnostics described above; do not require a new
   `feature_packet + policy_inputs` wrapper. Keep schema hashes and actual
   registry revision from the outer receipt and bind the stored response hash
   in the app, as the existing session already does.
2. Require the exact requested row set, nontruncation, complete numeric inputs,
   acceptable point-in-time evidence, and app-defined freshness. Check freshness
   against the underlying feature date, not the echoed decision cutoff.
   That September 5 version 1 result had to remain blocked.
3. Join stable Quant instrument IDs and symbols to the app's own universe and
   policy. Supply `cluster_id`, `structure_approved`, `position_limit_key`,
   caps, qualification, and risk-scale authority from app configuration.
   Factual `asset_type: etf` is not legal-structure or trading approval.
4. Select 21/63/252 volatility in the app, then populate its internal
   `approved_weighting_volatility` field. The reviewed shadow solver uses
   `1/sqrt(selected_volatility)`; preserve or explicitly revise that
   strategy choice in the app. Do not hide it inside Quant's estimator.
5. Replace the app's approval-bearing Quant fixtures in
   `test_local_warehouse_inputs.py` with standard raw feature/quality records.
   Keep fail-closed cases for missing inputs, unavailable historical evidence,
   receipt mismatch, and absent app policy.

The existing `options.get_surface_snapshot@2.0.0` interface separately exposes
retained underlying bid/ask evidence for SPY, QQQ, IWM, DIA, and the eleven
sector ETFs. Its `alpaca_option_surface_snapshot_v2` records include
`underlying_quote_bid_price`, `underlying_quote_ask_price`,
`underlying_quote_available_at`, `underlying_quote_state`, and
`underlying_quote_missing_reason`. The default v1 interface does not expose
these underlying quotes. This is a limited paper/indicative retained-data
path, with no freshness guarantee and no full 25-symbol coverage. Availability
is a local retention timestamp; the source-native quote timestamp is not in
the public record. It cannot establish a current executable quote. This
milestone inspected that interface's source only and did not access Alpaca
or an options store.

## FMP close metadata successors (September 13, 2026)

Registry **2.85.0**, catalog **2.31.0**, adds explicit metadata successors to the
existing price and analytical tools. All previous tool versions, input/output
schemas, defaults and calculation definitions remain available. Select the new
producer and consumer versions together; an older producer's unestablished
metadata is accepted by a new consumer without being silently reclassified.

| Tool | Select this version |
| --- | --- |
| `market.get_price_series` | `2.0.0` |
| `market.get_returns`, `market.get_forward_returns` | `2.1.0` |
| `market.technical_indicators` | `2.8.0` |
| `market.cross_sectional_performance` | `2.2.0` |
| `research.news_event_impact` | `2.0.0` |
| `timeseries.describe`, `timeseries.align`, `timeseries.correlation`, `timeseries.transform`, `data.quality_audit` | `2.1.0` |
| `stats.distribution_diagnostics`, `stats.covariance_matrix`, `stats.bootstrap_confidence_interval`, `stats.principal_components` | `2.0.0` |
| `econometrics.regression` | `3.1.0` |
| `econometrics.rolling_regression`, `econometrics.stationarity` | `2.2.0` |
| `econometrics.structural_breaks` | `2.1.0` |
| `research.point_in_time_panel`, `research.event_study`, `alpha.signal_diagnostics`, `research.walk_forward_backtest`, `research.robustness_suite`, `stats.multiple_testing`, `forecast.evaluate` | `2.1.0` |

`portfolio.get_etf_allocator_snapshot@2.0.0` already uses this close convention
and needs no further version change. Volume, quotes and corporate-action
retrieval tools retain their existing contracts.

The [FMP FAQ](https://site.financialmodelingprep.com/faqs) distinguishes
`close` (split-adjusted, excluding dividend adjustments) from `adjClose`
(split- and dividend-adjusted). The new reader binds this meaning only to
retained `/stable/historical-price-eod/full` captures with provider `fmp`,
normalization `stage10.fmp.daily_price.v1`, variant `fmp_full_eod_v1`,
provider-native currency, and matching instrument identity. It uses
`close_value` unchanged. No tool applies another split factor, substitutes
`adjClose`, or needs a split-history join for this convention.

A nonempty, fully bound close series reports:

- `adjustment_status: split_adjusted_excluding_distributions`;
- `source_price_field: close`;
- `price_adjustment_applied_by_tool: false`;
- `source_adjustment_binding: fmp_full_eod_close_split_only_v1`;
- the FMP documentation URL, selected source capture count, and
  `adjustment_vintage_status: single_provider_capture` or
  `mixed_provider_captures`;
- `source_publication_time: null`.

Empty or unbound close histories remain `not_established`. Open, high and low
remain unestablished under this close-specific evidence; volume semantics are
unchanged. Indicators report the close input's basis separately as
`source_close_adjustment_status`; their overall adjustment status remains
unestablished when other price inputs have an unestablished basis.
Cross-sectional and news-event records expose the selected close source basis.
Other analytical outputs retain their established lineage and warning
mechanisms; metadata is preserved wherever those outputs carry source series.
Structural schemas accept predecessor-shaped inputs; runtime semantic validation
requires a complete binding for every split-only claim and checks observation
flags/warnings for consistency. Multi-series analyses reject mixing established
and unestablished adjustment bases.
The numerical algorithms and their recorded kernel/transformation versions
are unchanged.

This convention describes retained provider values at each capture.
Multiple captures do not establish a common split-adjustment vintage.
Observation dates, local collection/availability timestamps, and original
provider publication times remain distinct. As-of selection excludes later
retained evidence; it establishes only the existing retained-local-capture
scope. These versions do not invent historical publication times, certify
historical split-vintage reconstruction, convert price returns into total
returns, fill missing sessions, or alter the consumer's investment policy.

Public verification on **2026-09-13 at 16:03:25 UTC** used the documented
`bin/quant-data-tools` launcher. SPY's August 1-31 selection returned 21
observations from four retained captures. Old/new OHLC values and return values
matched exactly; the new close and SMA outputs carried the split-only basis,
and `timeseries.describe@2.1.0` accepted the return output. Market database and
sidecar stamps were unchanged. Evidence is retained in
`.local/price-basis-metadata/public-verification.json`.

Validation: 29 focused tests passed, including existing indicator/news routes,
new public composition, malformed bindings, conflicting quality flags, mixed
input bases, empty histories and capture cutoffs. Exact `2.84 -> 2.85` and
`2.83 -> 2.84` generation, frozen/default contracts, generated-output and diff
checks passed. The final indicator and return-description consumers were
replayed through the public launcher after the consistency fixes. The full
offline suite was not run for this additive version change. No provider
requests, canonical writes or scheduler changes were performed.
