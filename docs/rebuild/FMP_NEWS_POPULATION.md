# Bounded FMP Stock-Latest News Population

Status: authorized forward reconstruction; offline fixture and live gates are
pending.  Nothing in this document is evidence of a live provider request,
publication, promotion, scheduling, or public exposure.

## Fixed population profile

The only future live request is one HTTPS `GET` to
`https://financialmodelingprep.com/stable/news/stock-latest`, with exactly
`page=0` and `limit=1000`.  The credential is read only from the process
environment variable `FMP_API_KEY` and is supplied as the `apikey` header.  It
is never put in a URL, registry, capture, receipt, log, exception, or public
surface.

The collector is `fmp.news.stock_latest`; its single profile is
`fmp.stock_latest.page0.limit1000.v1`.  It is manual-only, uses no retry,
redirect, fallback endpoint, second page, scheduler, consumer, tool,
dashboard, Atlas, export, promotion, or default target.  The sole future live
target is exactly
`/home/volatility/Python_Projects/Quant_Data_Infra/data/news.sqlite`; a wrapper
must require that explicit project root and target and reject symlinks or a
wrong store role.

## Response and capture contract

The transport accepts only HTTP 200, `application/json` (with an optional
charset), strict UTF-8 bytes, a 60-second timeout, and at most 64 MiB.  The
top-level JSON value is an array with zero through 1,000 rows; an empty array
is a terminal successful partial capture.  The raw bytes, SHA-256, byte count,
status, and MIME type remain private immutable evidence.

Every row must contain a nonempty string `symbol`, `publishedDate`, `title`,
and `site`; a string `text` (which may be empty); and an absolute HTTP(S)
`url`.  `image` may be absent, null, empty, or an absolute HTTP(S) URL.  Any
other provider fields are retained only by the raw response.  A malformed row
rejects the entire response before canonical article writes; rows are never
skipped.

`publishedDate` is retained verbatim.  A normalized timestamp exists only for
an offset-aware or `Z` ISO-8601 value.  Date-only and naïve values retain an
explicit precision and unknown-offset marker and are never coerced to UTC or a
time of day.

## Evidence and canonical ownership

Migration `news:0005_fmp_stock_latest`, after `news:0004_search_index`, owns
the following private relations:

| Dataset | Relations |
| --- | --- |
| `news.fmp.stock_latest_evidence` | `fmp_stock_latest_attempts`, `fmp_stock_latest_outcomes`, `fmp_stock_latest_captures` |
| `news.fmp.stock_latest_articles` | `fmp_stock_latest_articles`, `fmp_stock_latest_article_versions`, `fmp_stock_latest_capture_articles` |

Before the provider call, a resolved-news-lock transaction commits one
immutable intent for the exact profile and scope.  The lock is released before
network activity.  A pre-existing intent blocks another request, including an
ambiguous crash after a request; missing credentials and failed preflight make
no intent.  After a response or request failure, at most one terminal outcome
is appended.  A successful outcome has at most one raw capture.

Article identity includes the provider namespace, site, source URL, and symbol
so a provider duplicate attached to multiple symbols remains explicit.  The
immutable article version sequence records a hash of normalized mutable
content.  A completed prepared capture can be replayed without canonical
change; a new physical request is blocked by its intent.  This one-page feed
is always `partial`: an omission never creates a tombstone or retraction.

## Gates still required

Offline checks must use injected transport and clock with isolated temporary
stores.  They must prove exact one-request construction, release of the news
lock before transport, response rejection, raw retention, timestamp precision,
append-only lineage, target-only migration/register behavior, and no mutation
of project data.  Only after those checks and independent verification may a
separate explicit live authorization be considered.  A live run remains out of
scope for this reconstruction document.
