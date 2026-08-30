# Bounded FMP Stock-Latest News Population

Status: authorized forward reconstruction; offline fixture and live gates are
pending.  Nothing in this document is evidence of a live provider request,
publication, promotion, scheduling, or public exposure.

The frozen one-shot `news:0005_fmp_stock_latest` contract below remains
historical evidence.  It is not superseded, retried, or reinterpreted by the
separate current-feed successor documented at the end of this file.

## Frozen one-shot population profile

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

## Repeatable current-feed successor -- bounded manual proof

Registry `2.63.0` adds a separate current-news path, not a change to the
frozen one-shot contract: migration
`news:0006_fmp_stock_latest_current` (SHA-256
`bf8757bcc7679d1bd57408978eed996c9f4dad9c89339a52ca8adbeedbf83d30`),
datasets `news.fmp.stock_latest_current_evidence` and
`news.fmp.stock_latest_current_articles`, and manual-only collector
`fmp.news.stock_latest_current`.  Its exact profile remains one FMP
stock-latest page with `page=0` and `limit=1000`, but it reserves a single
immutable attempt for each UTC-hour poll slot.  It has no retry, fallback
endpoint, redirect, second page, or tombstone inference.

The successor accepts usable headline rows only: symbol, title, and absolute
HTTP(S) URL are required; site may be derived from the URL host; body and
publication timestamp may be absent.  Raw bytes and article bodies remain
private evidence.  The public `news.search@2.0.0` reader returns only retained
headline metadata with local-capture availability, lineage, warnings, and
truncation information.

The bounded 2026-08-30 16:00 UTC proof applied migration 0006 to
`data/news.sqlite`, made its one fixed page-zero request with no retry, and
retained 229 current articles. The immutable receipt and raw response remain
private. The broader eight-source successor's reviewed per-user timer was
linked, enabled, and started on 2026-08-30 at the hourly `:10` UTC cadence.
Activation did not run the service, contact a provider, or write a store.
