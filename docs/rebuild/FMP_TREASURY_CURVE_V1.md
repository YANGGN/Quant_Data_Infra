# FMP Treasury Curve v1

Status: Implemented; one bounded live population and local Inspector view complete
Decision date: 2026-08-21
Registry target: `2.23.0`, schema `1.8.0`

## Purpose and authority

The user selected the FMP U.S. Treasury curve as the next macro time series.
The user subsequently authorized the bounded code, one FMP request for
`2025-08-21` through `2026-08-21`, publication to `data/macro.sqlite`, and a
read-only local Inspector view. It does not authorize another provider window,
a scheduler, recurring refresh, public consumer, export, or deployment.

This scope is separate from the rejected BLS CPI archive candidate. Registry
revision `2.22.0` remains a rejected forensic identity and is not reused.
Treasury therefore becomes registry successor `2.23.0` without allocating a
new migration. The already accepted
`macro:0006_treasury_yield_curves` relations own the required curve identity
and immutable correction chain.

## Provider and request contract

- Provider: Financial Modeling Prep (`fmp`).
- Resource: stable Treasury Rates endpoint, `/stable/treasury-rates`.
- Request scope: one explicit inclusive `from`/`to` date window.
- Workload: one request, one attempt, at most 16 MiB and 20,000 response rows.
- Operation mode: manual-only. No recurring unit may adopt this collector
  without a separate explicit scheduler decision.
- Network work, response validation, and normalization finish before a
  physical-store lock or SQLite transaction is acquired.

The executable operation receives credentials through the existing explicit
credential boundary. Credentials, headers, and capture time never participate
in semantic identity and never appear in errors or receipts.

## Frozen series manifest

The v1 manifest is the 12-tenor restoration-plan curve. Provider extensions
remain unmodeled; in particular, a returned `month4` field does not silently
create a thirteenth canonical series.

| FMP field | Tenor | Series ID |
| --- | --- | --- |
| `month1` | `1M` | `macro.treasury.par_yield.1m` |
| `month2` | `2M` | `macro.treasury.par_yield.2m` |
| `month3` | `3M` | `macro.treasury.par_yield.3m` |
| `month6` | `6M` | `macro.treasury.par_yield.6m` |
| `year1` | `1Y` | `macro.treasury.par_yield.1y` |
| `year2` | `2Y` | `macro.treasury.par_yield.2y` |
| `year3` | `3Y` | `macro.treasury.par_yield.3y` |
| `year5` | `5Y` | `macro.treasury.par_yield.5y` |
| `year7` | `7Y` | `macro.treasury.par_yield.7y` |
| `year10` | `10Y` | `macro.treasury.par_yield.10y` |
| `year20` | `20Y` | `macro.treasury.par_yield.20y` |
| `year30` | `30Y` | `macro.treasury.par_yield.30y` |

Every response row must contain a valid date and all 12 declared fields.
Values are finite decimal percentages. A provider `null` is canonical
missingness with reason `source_null`; an omitted declared field rejects the
batch. Dates must be unique and inside the requested window. There is no fill,
interpolation, extrapolation, or synthetic business-day row.

Unknown provider fields are permitted but are not canonicalized. The exact
response hash and request metadata remain in source-artifact lineage, while
the normalized 12-tenor batch controls semantic replay.

## Identity, time, and corrections

- Curve identity is `(provider, curve_date, curve_variant)` with provider
  `fmp` and variant `par_yield`.
- Version identity adds tenor and correction sequence.
- `curve_date` preserves the source's date precision.
- FMP does not provide an authoritative publication timestamp in this
  contract. `available_at` therefore equals the aware UTC local
  `captured_at`, with datetime precision and `local_capture` history basis.
- Supported modes are `latest` and `as_of`; `first_release` is unsupported.
- An exact semantic replay produces zero persistent writes.
- A changed value or changed explicit missingness appends one immutable
  per-tenor correction and links it to the prior version. Unchanged tenors do
  not acquire new versions.
- Omitted dates never authorize tombstones.

## Registry and storage

The collector ID is `fmp.macro.treasury_yield_curve_history`; its handler is
`macro.fmp_treasury_yield_curve_history`. It reuses the four existing Stage 3
macro datasets that own generic evidence, canonical observations/catalog, and
the specialized Treasury curve tables. Those dataset declarations add the
collector ID but retain their relation ownership and temporal contracts.

No SQL resource, migration ID, ordinal, table, trigger, or canonical-store
migration is added. The `2.23.0` registry must project exactly back to accepted
`2.21.0` by removing the new collector from the collector inventory and the
four dataset collector lists, then restoring the registry version.

## Focused implementation gate

The implementation gate uses only deterministic responses and explicit
temporary roots/stores. It must prove:

1. all 12 tenors map to the frozen IDs and units;
2. date/window, duplicate-date, omitted-field, non-finite, and size bounds fail
   before a writer runs;
3. provider nulls remain explicit missingness;
4. source date precision is preserved and capture time is the availability
   boundary;
5. unknown fields do not expand the canonical manifest;
6. reordered/reformatted equivalent payloads are total no-write replays;
7. changed tenors append correct supersession chains while unchanged tenors do
   not;
8. foreign-key, integrity, snapshot-membership, and current-projection checks
   pass; and
9. the `2.23.0` registry and exact `2.21.0` projection validate.

The focused feature tests, adjacent registry compatibility tests, and
post-change Inspector tests passed. The one authorized request published 62
daily curves and 744 tenor versions covering `2026-05-26` through
`2026-08-21`. The provider returned only that subset of the requested window;
no additional historical request was made. The Inspector exposes the 12
tenors through a fixed, immutable read-only route.
