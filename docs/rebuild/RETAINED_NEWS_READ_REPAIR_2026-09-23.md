# Retained-news retrieval repair — September 23, 2026

Recent AAPL search and supported source-status reads now complete inside the
existing five-second tool deadline. Registry **2.92.0** is active; public tool
versions, input/output schemas and cursor format are unchanged. No provider
refresh, retry service, scheduler change or Codex_Driven_Research edit was made.

## Root causes and correction

Search fetched up to 200,001 joined version rows **per family**, then selected
versions, filtered, sorted and limited in Python. The profiled AAPL/limit-10
request rendered 37,492 FMP articles and 36,537 multi-source articles before
filtering. The joins read 38,818 and 46,406 version rows respectively. The
initial real public search returned `deadline_exceeded` after 21.273 seconds
end to end, despite its five-second tool budget.

Source status scanned capture metadata stored after large raw-response BLOBs.
Generic counts took about 13.46 seconds and latest-capture lookup 18.60 seconds
under profiling. A real two-source status request also failed its deadline,
taking 31.411 seconds end to end.

The quiet immutable reader checked descriptors/file stamps without coordinating
with publication. An ordinary writer could change the file between checks,
correctly producing `store_unavailable` / “news store changed during read.”
A controlled public invocation against a temporary store reproduced that race;
the quiet-period real reproduction produced deadline failures.

The fix pushes ticker/source/date/text filtering, keyset pagination and exact
remaining counts into SQLite. Existing symbol and per-article version indexes
are reused; only each family's requested page returns to Python. The latest
cutoff-eligible version is chosen independently of matching filters, so a
correction cannot make an obsolete matching version reappear.

Both search families share one descriptor and the existing physical-store lock.
Source status uses the same coordinated immutable reader. Descriptor, role,
sidecar and before/after integrity checks remain active. SQL progress checks
enforce the unchanged tool deadline.

One new index covers existing feed, capture time and capture ID fields:
`current_multi_source_captures_feed_time`, owned by
`news:0010_current_news_capture_lookup`. The established locked migration runner
applied it once in **42.566 seconds**, with its foreign-key gate passing.
All existing schema objects, prior migration entries and fact-table row counts
were unchanged. Only the index and its ledger entry were added.

## Successful public invocation and coverage

Send this envelope to
`/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call`
on standard input:

```json
{
  "api_version": "1.0",
  "tool": "news.search",
  "tool_version": "2.3.0",
  "arguments": {
    "symbols": [
      "AAPL"
    ],
    "start_date": "2026-09-16",
    "end_date": "2026-09-23",
    "limit": 10
  }
}
```

Final response completed at **2026-09-23T15:11:47.151Z**:

| Measurement | Result |
| --- | --- |
| Entire CLI duration | 1.035673 seconds |
| Tool receipt duration | 0.127391646 seconds |
| Article count | 10 |
| Newest publication (UTC) | 2026-09-23T15:00:20.000000Z |
| Oldest publication on this page (UTC) | 2026-09-23T06:32:07.000000Z |
| Returned source coverage | alpaca_benzinga: 10 |
| Matching retained articles | 167 |
| Truncation | applied=true; has_more=true; opaque next_cursor returned |
| Source links | All ten have HTTP(S) URLs with hostnames |
| Warnings | None |

Source status 1.0 with `{"source_ids":[]}` returned all eight supported sources
in **1.536183 seconds** inside the tool,
**2.351605 seconds** end to end.
Each latest retained outcome was `succeeded`; each latest successful capture
was `2026-09-23T15:10:42.092016Z`. These reads observed the next normal hourly
publication without triggering it. They are not live provider-health probes.

At fixed cutoff `2026-09-23T15:08:39.239055Z`, **17 pages covered all
165 articles**, exactly matching one untruncated limit-500 response.
There were **zero duplicates, zero missing or changed records**; every page's
remaining count matched. The subsequent normal hourly collection increased
the latest matching count to 167. These counts describe different cutoffs.

| Source | Articles in the complete 165-article as-of result |
| --- | ---: |
| fmp_stock_latest | 109 |
| fmp_press_releases | 6 |
| alpaca_benzinga | 50 |
| Other seven search sources | 0 matching this ticker/window |

Fifty articles had timezone-aware normalized publication timestamps, spanning
`2026-09-16T09:58:21.000000Z` through `2026-09-23T11:39:46.000000Z`.
The other 115 retained their original imprecise/naive publication fields.
Date bounds use source calendar dates, including the raw dates of those items.

The acceptance run made **23 public read-only tool calls**, plus three
manifest/describe reads, and **zero provider requests**. A report-only
source-field correction summarized saved responses without repeating pagination.

## Exact consumer versions and hashes

| Contract | Version |
| --- | --- |
| API envelope | 1.0 |
| Reviewed registry | 2.92.0 |
| Registry schema | 1.9.0 |
| news.search | 2.3.0 |
| news.get_source_status | 1.0.0 |
| Cursor envelope | 1 |

Registry SHA-256:
`69df849df7edca58a724021bd36661c6e7b0037d2980fe37f8da11645d4921ad`

Manifest SHA-256:
`c7b2ab4e95776ed2605793009d865badf491ebd6c937744e168dd2c931d5f516`

The manifest hash covers the exact UTF-8 stdout bytes of
`quant-data-tools manifest`, including the final newline. It differs from the
registry hash. Both complete versioned `describe` tool objects are identical
to their pre-fix objects; existing public catalogs are unchanged.

| Tool | Schema | SHA-256 |
| --- | --- | --- |
| news.search@2.3.0 | input | `7e4b5d05b65e4f5fcc750b3cb83f0e8ae3ae6a4333dbaff1863c5927daa542b3` |
| news.search@2.3.0 | output | `60dd09bc0bcfc2a07a77de6b9d4110f609cd7e36b8c2e342bff2282bbf62d528` |
| news.get_source_status@1.0.0 | input | `2d6dfde55b1842b9849e443eb4730ba73b56e86f21cb0ff855e9aabce48e4017` |
| news.get_source_status@1.0.0 | output | `0e2caa730246ec192c41dc7c7a5662e9af44e9069455d6addd32e3f256f141a0` |

Schema IDs:

- `urn:quant-data:tool:news.search:input:2.3.0`
- `urn:quant-data:tool:news.search:output:2.3.0`
- `urn:quant-data:tool:news.get_source_status:input:1.0.0`
- `urn:quant-data:tool:news.get_source_status:output:1.0.0`

Immediate predecessor registry 2.91.0 remains exact at SHA-256
`893c9bf9e93a4062b2a20cebf5928d29488600fd769fc88d107b6849c126af19`.
Migration 0010 SHA-256:
`63cf675540c6a89c19ecfce4ffc71910d8f5bde649842726caf725bef32794c2`.

## Validation and files changed

The fresh independent reviewer passed **36 focused tests in 32.199 seconds**:
news modules `test_retained_read_repair`, `test_current_repository`,
`test_current_multi_source_repository`, `test_tool_repository`; tool-platform
modules `test_news_access_v2`, `test_news_access_v21`, `test_news_research_tools`,
and `test_website_news_access`.

The new regression module covers latest eligible versions before filters,
newly captured old articles, as-of pagination despite later corrections, bounded
record materialization, indexed status, empty/unsupported/failure distinctions,
last successful capture, legacy-reader race reproduction, coordinated real
fixture publication, retained integrity checks, SQL deadlines, migration
checksum/rollback/replay and exact registry/public-contract compatibility.

An additional **192 independent old/new comparisons** matched records, receipts
and selection semantics, including date/offset cutoffs, imprecise timestamps,
ties and Unicode text. Generated-artifact validation passed against the active
registry, as did scoped whitespace checks. This is a focused gate, with no
full-suite claim for unrelated workspace changes.

Changed production and test files:

- `quant_data/news/selection_sql.py` and `read_session.py`.
- `quant_data/news/current_repository.py`, `current_multi_source_repository.py`,
  `tool_repository.py`.
- `quant_data/tool_platform/news_access.py` and `news_research.py`.
- `quant_data/migrations/news/0010_current_news_capture_lookup.sql`,
  `quant_data/news/lookup_registry.py`, `config/system_registry.json`.
- `quant_data/registry.py`, `quant_data/tool_platform/generate.py`,
  `quant_data/company/short_description_registry.py` (version/hash allowlists
  and exact predecessor chain only).
- `tests/news/test_retained_read_repair.py`.

Documentation: `docs/LOCAL_AGENT_TOOLS.md`, this report, and rebuild
`MIGRATION_RECONSTRUCTION.md`, `SCHEDULING_AND_LOCKING.md`,
`CURRENT_OPERATING_ENVELOPE.md`.

Evidence directory: `.local/retained-news-repair-20260923/`.
Key receipts: `canonical-index-receipt.json`, `acceptance-summary.json`,
`final-read-summary.json`, `final-timed-reads.json`, `manifest-after.json`,
both `*-describe-after.json`, exact public requests/responses, and
`independent-review-record.md`.

## Remaining limitations

- Reads serialize on the existing exclusive lock. Acquisition waits at most
  one second; sustained contention or a long migration can return `conflict`.
  Broad searches can still exceed the five-second deadline. No retries are added.
- Exact retained counts do not guarantee complete provider history. Ticker
  associations are retained provider metadata, not inferred entities.
- Publication precision is preserved: unknown/date-only/naive instants follow
  known instants. Capture time never substitutes for publication. The first
  ten results do not establish that items without timezone information are older.
- `latest` pages can change during collection; use one fixed `as_of` and
  unchanged filters for reproducible pagination.
- Source status 1.0 supports eight sources; search 2.3 additionally supports
  `finviz` and `financialjuice`. Unsupported status sources fail validation.
  Zero matching articles, unreadable stores and retained collection failures
  remain distinguishable. Unrecorded scheduler/credential failures cannot be
  inferred from a prior successful capture.
- Concurrency was tested with actual publication in temporary stores. Real
  post-collection reads succeeded; no precisely timed overlap with a live
  writer is claimed. Source URLs were structurally checked, not fetched.
