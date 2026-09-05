# Fetch repairs - 2026-09-05

The user requested repairs for H.8/SLOOS, live corporate actions and analyst
expectations, and stale CFNAI, with available data wired into live fetching.
The work began September 4 in New York and continued after midnight.

## Completed finite publication

All six publications used already-retained responses, existing physical-store
publishers, and no network requests during publication:

| Capture | Canonical result | Coverage |
| --- | --- | --- |
| H.8 | 7 series, 157 versions | 4 weekly series through August 26; 3 monthly series through July |
| SLOOS | 6 series, 18 versions | Through 2026-Q3 |
| CFNAI via FRED | 7 new/corrected versions on the existing series | Through July 2026 |
| AAPL dividends | 92 action versions | Provider-returned history |
| AAPL splits | 5 action versions | Provider-returned history |
| AAPL annual estimates | 200 observations across 10 periods | Explicitly partial/capped |

The macro request scope was 2026-01-01 through 2026-09-04. Company publication
was limited to AAPL. Every exact replay returned unchanged and zero written
rows. These completed manual populations must not be silently repeated.

Private evidence, original capture times, publication/replay receipts, and
post-validation live in
`data/.operations/fetch-repairs/2026-09-05T03-39-08Z/`.
Company content-addressed responses and SEC discovery additionally live in
`data/.operations/company-market-refresh/blobs/`. Company availability is the
later of response and identity-discovery capture: 2026-09-05T03:48:28.495245Z.
Original earlier FMP response times remain in the preflight receipt.

## Macro source repairs and recurring scope

The H.8/SLOOS operation now reuses the existing standard-library FRED CSV
transport. All 13 fixed series returned accepted HTTP 200 CSV responses.
This supersedes the September 4 failed diagnostic for current eligibility;
the earlier attempts remain historical evidence.

H.8 normalization is `federal_reserve_h8_v2`. BUSLOANS, REALLN, and CONSUMER
are monthly with full calendar-month bounds; the other four series are weekly.
The original v1 fixture and semantic golden remain retained. A new synthetic
mixed-frequency fixture checks v2, invalid monthly dates, and exact replay.
Primary references: [BUSLOANS](https://fred.stlouisfed.org/series/BUSLOANS),
[REALLN](https://fred.stlouisfed.org/series/REALLN), and
[CONSUMER](https://fred.stlouisfed.org/series/CONSUMER).

Both Chicago Fed workbook URLs returned identical 43,989-byte workbooks ending
in April (SHA-256
`ec906c1eb4f9f173042da277f327e953de01d4b0049cffaec5667db98623e4ad`).
The [official FRED CFNAI feed](https://fred.stlouisfed.org/series/CFNAI)
provided a 139-byte CSV through July, SHA-256
`4a9bb2375e7598e1a06b16f5d5ad140c9f40667b215a9e7ecf60e957fd479de4`.
The live route now uses one bounded FRED CSV request, retaining Chicago Fed as
origin, FRED as distributor, and `chicagofed_cfnai_fred_v1` provenance.

The existing macro wrapper now runs 29 operations with a 94-request cap.
H.8/SLOOS contribute 7/6 requests and use the previous quarter's start through
the current quarter's end, preserving the latest available release during
quarter-boundary lags. CFNAI uses 2026-01-01 through the current quarter's end.
The enabled weekday 18:30 America/New_York timer keeps its existing cadence,
no-retry and no-catch-up behavior. The inspected next trigger was September 7.
No systemd unit was created, edited, enabled, started, or reloaded during the
initial data-repair and publication phase. The separately approved company
activation is recorded below. Unrelated earlier macro failures were not retried.

## Company collector and approved schedule

The new zero-argument `quant_data.operations.company_market_refresh` wrapper
reads at most 700 retained FMP equities, makes one SEC discovery, and requests
dividends, splits, and one annual-estimates page per unambiguous symbol whose
CIK already exists. The inspected roster had 519 equities, 516 matching symbols
and 513 existing CIKs; AVB, EA, and EQR were unmatched. No identities are created.

The bounds are 2,101 requests, 128 MiB, and 30 minutes per run; each FMP response
has a 1 MiB bound, with at least 0.4 seconds between request starts and no retry.
Actions cap at 1,000 source rows; annual estimates cap at ten source periods.
Partial inputs publish available data but report partial coverage, warnings,
and a nonzero aggregate exit. Missing currency is stored as
`SOURCE_UNSPECIFIED` for dividend cash and separate source-unspecified metric
units for estimates. USD, fiscal starts, accounting basis, and publication
timestamps are not inferred.

Action identity uses issuer, instrument, action kind, and ex-date. Corrections
to amount, record date, payment date, and declaration date supersede the same
event; same-key ambiguity fails closed. Ticker spelling changes under one
instrument preserve identity. Continuity across distinct Stage 10 instrument
IDs remains outside scope.

Initial unit creation was blocked by automatic approval review because the
project requires a new explicit scheduler decision. On September 5, the user
explicitly approved the proposed weekday 19:00 America/New_York schedule and
the fixed limits above. The service and timer were then created under
`deploy/systemd/`, linked into the host user unit directory, and reloaded.
Only the timer was enabled and started. It is non-persistent with no
catch-up/retry; the service has `Restart=no` and a 31-minute host timeout.
The completed AAPL pilot grants no manual repeat authority.

Post-activation inspection confirmed the timer is enabled and active/waiting,
with its first scheduled trigger Monday 2026-09-07 at 19:00 EDT (23:00 UTC).
Its last-trigger field is empty, and the service remains inactive/dead with no
execution timestamps. All 22 before/after filesystem stamps for the four
stores, their sidecars, and company operation state matched; the company state
path set was unchanged. Activation made no provider requests and opened no
canonical databases.

Both unit files passed `systemd-analyze --user verify`; all eight company
wrapper tests passed. An independent verifier found no issues with the
approved cadence, limits, hardening, physical locks, or timer-only activation
plan. Private `company-timer-activation.json` in the evidence directory above
records the approval, unit hashes/links, activation commands, observed systemd
state, and before/after stamps. No full test-suite run was needed for activation.

## Registry and validation

Registry 2.69.0, schema 1.9.0, has 44 migrations, 59 datasets, and 66 collectors.
Only two private company collector bindings were added to existing datasets.
Current SHA-256:
`2e9c3e4d2bfc263735a1e9c875d2091210065e0a375a0a0e0c420839a03c774f`.
Exact projection restores registry 2.68.0 at
`9b59f6b643e4cff7390559763c8532215ac9927a1f3119870385127af3a6a27e`.
Both generated tool catalogs and the schema/migration inventory are unchanged.

Independent verification closed mixed cadence/month bounds, registry and
generated artifacts, dividend correction identity, partial-estimate reporting,
and quarter/year boundary scope. It ran 37 scoped tests, the generated-artifact
checksum test, and the final six macro-wrapper tests, plus isolated retained
publication/replay and correction reproductions. Root repair regression tests
also passed. The full repository test suite was not run.

Canonical post-validation is recorded in the private post-validation receipt.
A whole-store macro quick-check exceeded its 45-second limit; this is a bounded
scan limitation, not evidence of corruption. No full-store integrity guarantee
is claimed.
