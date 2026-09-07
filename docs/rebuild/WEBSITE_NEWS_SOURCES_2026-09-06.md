# Finviz and FinancialJuice current news

Accepted scope: September 6, 2026. The user approved Finviz and FinancialJuice
collection, their addition to the existing live fetcher, and a forward migration
extending the shared news tables for simplicity and maintenance.

## Storage and identity

`news:0009_website_source_extension` extends only the source/provider allowlists
in `current_multi_source_attempts`, `current_multi_source_captures`, and
`current_multi_source_articles`. The existing migration runner rebuilds these
three tables in one transaction, preserves every old row and original guard,
checks all foreign keys, and records the new checksum only on success.
Migrations 0001–0008 remain unchanged. No website-specific table or dataset is
introduced. News remains owned by `data/news.sqlite`.

Both sources use the existing evidence, article identity, article version,
capture membership, outcome, and symbol tables. `feed_id` identifies the
source. Finviz identity is the canonical destination URL; FinancialJuice
identity is its numeric RSS GUID, verified against its article URL. Headlines
from different sources retain separate provenance even if their text matches.
A correction appends a version of the same article. Raw response bytes remain
private; public readers return headline metadata and available summaries.

## Fixed requests and time

Each normal hourly invocation adds exactly one credential-free GET per source:

- Finviz: https://finviz.com/news, server-rendered headline listing.
- FinancialJuice: https://www.financialjuice.com/feed.ashx?xy=rss, the official
  RSS feed linked from its homepage.

Each request is limited to 4 MiB, 1,000 listing rows, and 60 seconds. There are
no redirects, retries, cookies, credentials, pagination, or destination article
fetches. A supervised request process enforces the deadline across DNS, TLS,
headers, and body reads; expiry terminates and reaps that single process. A challenge, login page, JavaScript-only shell, conflicting duplicate,
or malformed listing fails closed and is recorded as a source-local failure.
The remaining feeds continue.

The parser preserves source-native publication precision. Finviz time-only and
month/day labels retain their raw text and unknown precision; capture time is
never substituted for publication time. A complete date stays date-only.
FinancialJuice offset-bearing RSS dates normalize to UTC. Availability remains
the local timestamp sampled after the website response arrives, independently
of the hourly poll slot, so as-of queries cannot see later captures.

Network work and parsing finish before acquiring the physical news-store
writer lock. Exact semantic replay against the latest capture causes zero
canonical writes, including attempts, outcomes, captures, and versions.
Tracking parameters, markup, listing order, and capture clocks do not alone
create a version. A changed listing or correction is published atomically.
Capture identity retains its poll scope; comparison against the latest capture
contents suppresses only an exact replay. A to B to A creates three versions.

## Fetcher and readers

The existing `current_news_refresh` batch appends `finviz` and
`financialjuice` after its original eight sources. Its authorized hourly
`:10` UTC cadence and unit definitions remain the scheduling mechanism.
The two sources add two requests to the existing bounded workload; the
generic collector ceiling is 24, plus one separate FMP stock-latest request.
Missing FMP or Alpaca credentials do not disable the website sources.

`news.search@2.3.0` and the Inspector news page include both website sources.
Versions 2.1 and 2.2 retain their original eight-source scope, including when
no source filter is supplied. Version 2.3 shares the existing bounded keyset
pagination implementation. Other frozen news research/source-status tools
retain their documented source sets.

## Validation and activation evidence

Required checks cover populated migration preservation, rollback, immutable
guards, correction history, capture cutoff, exact no-write replay, fixed
transport bounds, old reader compatibility, and pagination across all sources.
The full offline suite and a fresh independent migration review are required
before canonical activation. Runtime evidence and finite population receipts
are recorded separately in the operating envelope after execution.

The 2026-09-06 offline acceptance gate passed all 1,625 unique cases across
retained and resumed runs. Original failures and interruptions remain recorded;
all affected cases passed explicit rechecks. Independent review also confirmed
that the compact current registry reproduces the unchanged historical profiles.
The saved real responses passed publication, public-reader, and zero-write replay
checks in temporary stores: 180 Finviz and 100 FinancialJuice headlines.


Canonical activation completed at `2026-09-06T18:54:30.098059Z`: news
migration 0009 preserved all existing rows and guards, and the two saved
responses added 180 Finviz and 100 FinancialJuice articles without a new
provider request. Both source-filtered public readers and live Inspector pages
passed. The existing hourly timer was observed waiting for its next normal
19:10 UTC slot with the ten-source module installed; no manual timer action or
fresh website poll was performed. Dated receipts and the no-repeat scope are
in the [operating envelope](CURRENT_OPERATING_ENVELOPE.md#september-6-2026--shared-finviz-and-financialjuice-activation).
