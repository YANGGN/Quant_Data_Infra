# Expanded current-news universe — September 11, 2026 UTC

Status: active. On September 12 at 01:28 UTC (September 11, 21:28 Toronto),
the user-requested reduction to ten tickers per Alpaca batch was verified against
the active host roster. It now produces 235 batches for 2,343 mapped symbols,
while retaining 50 articles per page. The first scheduled run with this setting
is September 11 at 22:10 Toronto (September 12 at 02:10 UTC), not yet observed.
The dated original expansion records below describe the earlier 50-ticker setting.

## Authorized outcome and scope

The user asked to implement news for the expanded selection. This covers ongoing
current-news acquisition for the 2,248 selected securities, while retaining 96 ETFs
and 15 index targets. Historical news backfill has no specified date range and is
not included. No existing article or source-symbol association is rewritten.

The prepared roster has 2,359 matching instruments. Alpaca acquisition covers only
exact provider-evidenced active equity/ETF symbols, with explicit unresolved,
ambiguous or inactive gaps and separate unsupported index targets. Catalogue
presence establishes symbol identity, not that every symbol has a news article.
No dot/dash conversion, sibling share class or issuer substitution is inferred.

## Identity preparation

The existing Alpaca asset catalogue endpoint is documented at
[Get Assets](https://docs.alpaca.markets/us/reference/get-v2-assets-1).
The prepared lookup is exactly one GET to the existing paper environment's
`/v2/assets?status=active&asset_class=us_equity`, using the existing
`ALPACA_API_KEY` and `ALPACA_API_SECRET` resolver. Its limit is 8 MiB and a
supervised 60-second response deadline, with no redirects or retries. An attempt
marker is durable before dispatch; an uncertain or oversized response remains
held and is not repeated. The repository requires explicit finite provider scope,
and the user approved this exact lookup with 'ok go ahead' on September 11.
It completed once with HTTP 200, 6,454,425 bytes and 14,315 catalogue entries;
there were no retries.

All 96 ETFs already have original FMP response evidence. The prepared mapping
reuses 106,105 bytes of SHA-verified retained price responses and their original
capture IDs/timestamps. No FMP lookup is needed. The derived ETF manifest records
its original independently pinned Stage10 membership IDs. Existing manifest and
mapping publishers own eventual publication to market.sqlite; news.sqlite remains
the owner of articles and raw news captures. No schema migration is involved.

## Hourly operation

The existing hourly :10 UTC timer, 30-minute service timeout, no-catch-up behavior,
source parsers and publishers remain in place. At the current population ceiling,
there are at most 47 Alpaca batches of 50 symbols plus nine shared-feed requests:
56 requests per normal invocation. Global FMP, official and website feeds are not
multiplied by the number of tickers. The shared FMP allowance is unchanged.

The expanded Alpaca step deducts elapsed earlier-feed time before admitting its
batches and reserves 180 seconds for the two final website feeds and local work.
This is a request-start reservation. Existing non-website transports retain their
socket timeouts; website completion before the host timeout is not guaranteed.
The host's existing service timeout remains the final execution cap.

Each Alpaca batch requests only its first page of at most 50 articles. Reports
retain full-page counts, deferred batches, mapping gaps, unsupported indexes,
request/response bounds and unproven time-window completeness. The source profiles
are current snapshots, not complete historical news archives. Existing source
versions, source-native symbols and as-of reader contracts are preserved.

## Activation and evidence

The news-only cutover is prepared to run after the lookup, exact mapping review
and independent source review. It publishes the retained ETF membership/FMP map
and separate selected-equity/retained-ETF Alpaca maps through existing publishers.
It verifies existing instrument/membership/FMP hashes and mapping foreign keys,
then changes only news.mode in the latest binding configuration. Other agents'
price, company, transcript and allowance settings are preserved. No manual news
fetch or recurring-unit start/reload is included; the next normal slot uses the
active binding once the cutover succeeds.

Preparation and operational receipts: `data/.operations/news-expansion-20260911/`.
Source preimages, driver, validation and logs: `.local/news-expansion-20260911/`.
At preparation, provider requests, canonical writes and schedule changes are zero.
An eventual `activation-result.json` is required before claiming activation.

Validation passed 20 focused tests and 22 adjacent compatibility tests. The focused
suite includes exact provider identity and gaps, 2,344 equity/ETF symbols across
47 batches, singleton global feeds and the invocation request-start budget.
Compatibility covers existing news captures/readers, symbol batching, replay and
fixed FMP refresh behavior. A fresh independent verifier passed the scoped source
review and independently ran ten overlapping memory-only tests. No full-suite pass
is claimed or required for this private binding/domain-local change.

The activation helper also passed three isolated interruption/recovery tests
(45 unique author-run tests in total). A second independent source review closed
the interrupted-cutover issue: a durable cutover record permits recovery before
any publisher, and a failed coverage check restores only the matching news
binding while preserving unrelated settings. These checks establish preparation/recovery behavior. Actual activation evidence
is recorded below.

## September 11 activation result

The user-approved single catalogue request completed at 13:12:26 UTC. Exact
provider identities resolved all 2,248 selected equities and 95 of 96 retained
ETFs. IRBO has no exact entry in the active Alpaca catalogue and remains an
explicit unresolved gap. The 15 index targets retain matching coverage but are
unsupported as Alpaca equity/ETF request symbols.

At 13:13:23 UTC the existing publishers stored the derived 96-ETF membership,
its FMP evidence mapping, and separate selected/retained Alpaca mappings.
The active host reader confirmed 2,359 matching instruments and 2,343 Alpaca
request symbols in 47 batches. Existing instrument, selected membership and
selected FMP mapping hashes remained identical; collection mapping foreign-key
checks passed. A fresh comparison of the latest binding file with the cutover
preimage confirmed that only news.mode changed from prepared to active.

The existing timer is enabled/active/waiting, next due at 14:10 UTC (10:10
Toronto). Its 13:10 UTC run completed before activation. No manual news fetch,
schedule change or historical backfill was performed, and no expanded scheduled
fetch completion is claimed yet. Shared/global feeds remain single requests.
The first-page-only and request-start timing limitations described above remain.

Operational evidence is retained in assets-result.json, mapping-review.json,
the four publication receipts, activation-result.json and post-activation-check.json
under data/.operations/news-expansion-20260911/. The reviewed source hashes
still match the independent review receipt.

## September 12 UTC / September 11 Toronto — ten tickers per batch

The user explicitly requested reducing the batch size to ten after reviewing the
request-count tradeoff. This supersedes the initial 50-ticker/47-batch setting for
the active selected universe. All 2,343 mapped symbols are retained exactly once:
234 full ten-ticker batches and one three-ticker batch. The nine shared feeds
remain singleton requests, so the current invocation cap is 244 requests.

The provider page size remains 50 articles, with one page per batch and no retry
or historical backfill. Selected request starts are separated by at least 0.5
seconds to reduce burst pressure. Pacing consumes the existing invocation budget;
after waiting, the runner checks that time remains before starting a request.
The 30-minute service limit, final-feed reservation and explicit deferred-batch
reporting remain. This local pacing is not an account-wide rate-limit guarantee.
The prior 6,600-symbol structural ceiling now corresponds to at most 660 groups
of ten; it does not authorize a larger selection.

Forty focused and adjacent offline tests passed in 8.063 seconds, covering full
235-request routing, 50-article wire parameters, singleton global feeds, gaps,
request-count/group-size bounds, pacing after failure, deadline overruns, replay
and reader compatibility. No full suite or new independent review is claimed for
this domain-local adjustment. A quiet immutable host read verified the unchanged
selection fingerprint, both Alpaca mapping IDs and IRBO gap, and the exact new
batch partition. No provider request, canonical write, configuration-file change,
manual run or recurring-unit mutation was performed for this adjustment.

The timer was enabled/active/waiting with the next normal slot at 22:10 Toronto.
The ten-ticker setting's first provider run remains unobserved. Source preimages,
the reviewed task diff, offline test log and host validation are under
.local/news-batch10-20260912/ (tests.log and validation.json).
