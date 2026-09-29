# Sharadar direct compatibility check — September 12, 2026

Status at comparison: Complete; conditional source replacement not performed.
Later decision: The user approved implementation; see
[the direct migration record](SHARADAR_DIRECT_MIGRATION_2026-09-12.md).
Decision: The user requested a comparison of each existing Nasdaq-delivered Sharadar
pull against the direct service, using the existing named SHARADAR_DIRECT_API
credential, and replacement only if the formats match.

The sampled fields are largely compatible, but the responses are not a drop-in
match. All eight direct data samples fail the current parser envelope checks.
Existing source configuration, canonical history and scheduler configuration
were left unchanged. A versioned adapter and storage-compatibility migration
need to be agreed before source replacement.

## Scope and actual execution

The existing executable inventory comprises SF1 fundamentals in six dimensions,
SF1 definitions (INDICATORS), TICKERS identity preparation, and their three
metadata dependencies. Other Sharadar tables listed in the wider storage-design
inventory are proposals, not additional existing pulls to replace.

At 2026-09-12 05:01:45–05:01:48 UTC, this check made exactly **11 GETs**, with
**11 HTTP 200 responses**, **71,830 bytes** retained and no retry:
three public SQLite schema downloads, one AAPL ticker sample, one fundamentals
definition sample, and six AAPL fundamentals samples. Each request was capped
at 512 KiB and 30 seconds; the finite script had a 330-second dispatch cutoff.
Fundamentals used explicit from=2025-01-01, to=2025-12-31, limit=2 and one
dimension per request. This is a format sample, not a historical population.

The named credential reader loaded only SHARADAR_DIRECT_API from the project
.env. The key was sent only to api.sharadar.com in the x-api-key header.
No credential appears in the retained request identities or this report.
No new Nasdaq request, canonical-store open, publication, migration, scheduler
operation, historical backfill, commit or push occurred.

The comparison reused the three SHA-256-verified Nasdaq metadata responses
captured September 9 in the previously approved bootstrap. Financial values
were not compared against a contemporaneous Nasdaq data download; value
equality, full-universe entitlement and complete historical coverage remain
unproven.

## Pull-by-pull result

| Existing pull | Direct counterpart | Sample rows | Result |
| --- | --- | ---: | --- |
| SF1 / ARQ | fundamentals / ARQ | 2 | 112 fields: 111 identical names; datekey maps to date |
| SF1 / ARY | fundamentals / ARY | 1 | Same 112-field mapping |
| SF1 / ART | fundamentals / ART | 2 | Same 112-field mapping |
| SF1 / MRQ | fundamentals / MRQ | 2 | Same 112-field mapping |
| SF1 / MRY | fundamentals / MRY | 1 | Same 112-field mapping |
| SF1 / MRT | fundamentals / MRT | 2 | Same 112-field mapping |
| TICKERS, table=SF1 | tickers, table=fundamentals | 1 | All 28 field names match; table value and response layout differ |
| INDICATORS, table=SF1 | descriptions, tablename=fundamentals | 112 | All seven field names match; table/indicator values and layout differ |
| SF1 metadata | schema/fundamentals, plus descriptions | — | SQL schema and field definitions replace the Nasdaq JSON metadata envelope |
| TICKERS metadata | schema/tickers | — | SQL schema; cannot be passed to existing metadata parser |
| INDICATORS metadata | schema/descriptions | — | SQL schema; cannot be passed to existing metadata parser |

The actual direct JSON uses lowercase isfilter, isprimarykey and unittype,
matching the retained Nasdaq metadata. Some documentation renders those names
in mixed case; no field rename is needed for the sampled definitions.

The direct definitions cover all 112 returned fundamentals fields. Their primary
key is ticker, dimension, date, reportperiod, corresponding to the existing
four-field key after the explicit datekey/date mapping. Advertised direct
filters are calendardate, dimension, lastupdated and ticker. Nasdaq metadata
also advertises datekey and reportperiod. Availability of equivalent direct
date filters was not experimentally tested.

[Direct fundamentals documentation](https://sharadar.com/docs/fundamentals),
[ticker documentation](https://sharadar.com/docs/tickers), and
[definition documentation](https://sharadar.com/docs/descriptions) support the
endpoint and field mappings above.

## Differences that prevent a URL/key substitution

1. **Response layout.** Nasdaq returns datatable.columns, datatable.data arrays
   and meta.next_cursor_id. Every direct sample returns count and data objects,
   without column declarations or cursor metadata.
2. **Dates and table names.** The direct source uses date instead of datekey,
   and fundamentals instead of SF1 in ticker and definition scope values.
   Original source names and raw bytes must remain recoverable.
3. **Pagination.** Direct uses limit and skip/offset; existing code walks
   qopts.cursor_id. In the limited fundamentals samples, count equals the
   returned page length. It must not be treated as proof of complete history.
   Stable ordering, terminal-page detection and incomplete-snapshot handling
   need their own tested direct contract.
4. **Date defaults.** The direct documentation describes a one-year default
   lower bound and prior-day upper bound. Requests must explicitly preserve
   the intended historical/update-date scope. The interaction between a
   lastupdated lower bound and the implicit date window was not tested.
5. **Numeric representation and metadata.** Direct JSON includes integer
   strings and JSON decimal numbers. All sampled values are accepted by the
   existing exact numeric/date/text conversion functions when matched by
   field. The direct schema and descriptions are not a replacement for
   Nasdaq's metadata bytes or type-contract hashes.
6. **Stored provenance.** Applied company migrations 0012 and 0014 constrain
   source row pointers to /datatable/data/N; the SF1 schema pins
   nasdaq_sf1.v1. Publishers and source-key hashes include Nasdaq channel/schema
   contracts. Direct original rows live at /data/N. Wrapping direct rows in a
   synthetic Nasdaq envelope and calling those bytes original evidence would
   violate the existing evidence contract.

[Direct querying](https://sharadar.com/docs/getting-started) documents pagination
and defaults. The [existing Sharadar storage contract](SHARADAR_STORAGE_DESIGN_2026-09-08.md)
requires channel-specific adapters, original evidence, preserved dates and
metadata-pinned identity.

## Concrete migration required

The proposed next change is limited to the existing three Sharadar data
families, selected universe and six SF1 dimensions:

- Add direct request/response handling for SHARADAR_DIRECT_API, explicit query
  bounds, native object rows, exact decimal parsing and tested page completion.
- Retain original direct response/schema/definition bytes, direct source
  pointers and a distinct versioned delivery-channel contract. Map date to the
  existing source-date meaning explicitly; preserve its source spelling.
- Add the necessary schema evolution without editing applied migration bytes.
  Define how readers select direct data after the cutover while keeping Nasdaq
  history, availability times, permanent identities and replay evidence intact.
- Update the registered collector/credential and selected acquisition paths
  only after compatibility checks pass. Never feed direct bytes into retained
  Nasdaq request journals or replay old incomplete manifests against a new host.
- Verify exact replay, cutoff behavior, AR/MR separation, missingness, complete
  pagination, definition scope and reader behavior using isolated fixtures.
  Schema/shared-safety changes require the full offline suite and fresh
  independent verification under the project test strategy.

This is a proposed migration, not an implemented source switch. The current
request's exact-format condition failed; the necessary adapter/schema scope
is returned to the user for a decision. No additional paid requests or
population repeats are included in the completed comparison.

## Validation and retained evidence

- Three original Nasdaq metadata hashes verified.
- 147 field mappings checked individually: 112 SF1, 28 TICKERS, seven INDICATORS.
- 1,932 sampled cell conversions passed: 1,120 fundamentals, 28 ticker,
  784 definition cells. Every sample row matched its requested ticker,
  dimension/date window or fundamentals table scope.
- All eight direct data samples were rejected by existing production parser
  entry points with their expected envelope-validation errors.
- The direct primary key and 112-definition coverage were checked locally.
- Production code was not changed; unrelated test suites were not run.
  This investigation is not independent verification of a migration.

Private evidence:
[request receipts](../../.local/sharadar-direct-comparison-20260912/summary.json),
[all field mappings](../../.local/sharadar-direct-comparison-20260912/FIELD_COMPARISON.md),
[machine-readable comparison](../../.local/sharadar-direct-comparison-20260912/field-comparison.json).
The private probe retains an exclusive attempt journal and must not be rerun as
a retry. The offline comparison can reuse the existing captured files without
network or credential access.
