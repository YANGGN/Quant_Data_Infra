# Current Operating Envelope

Status: Current operational routing snapshot; non-authorizing
Reconciled: 2026-08-21

## Purpose

Read this document before any provider, credential, network, scheduler,
canonical-store, migration, promotion, retirement, deployment, public-
exposure, or destructive operation.

This envelope consolidates existing user decisions and evidence. It grants no
new execution authority. Code, credentials, registry declarations, unit files,
database files, and historical instructions establish only that artifacts
exist. If an action is not explicitly permitted by the current user request,
stop. A separate focused contract is required only when the
[fast-path](FAST_PATH_DEVELOPMENT.md) heavy-workflow criteria require one;
otherwise the current request plus a stated bounded operational scope is
sufficient authority.

Detailed receipts, hashes, counts, request rules, and historical limitations
remain in the linked contracts and evidence. This file records only the
current control boundary.

## Authority and working-state disclosure

The authority order is the one in the
[rebuild index](README.md): explicit user decisions, accepted ADRs, focused
contracts, the roadmap, and then recovery history.

The current accepted registry is revision `2.21.0`, schema `1.8.0`. The former
working `2.22.0`/`validated` candidate is rejected under
[ADR 0011](../adr/0011-retire-proposed-bls-cpi-release-archive.md). It never
established provider, canonical-store, consumer, scheduler, or live-population
authority. Because it never entered the active configuration lineage,
byte-exact restoration of `2.21.0` is a candidate discard rather than a new
registry revision.

An explicitly authorized 2026-08-21 immutable read-only proof confirmed that
the current canonical macro store identifies itself as `macro`, ends at the
exact accepted ordinal-16 FMP wholesale migration under registry `2.21.0`,
and contains no `0017` ledger collision or BLS original-release archive
relation. Main/WAL/SHM/journal physical stamps were unchanged and all
sidecars were absent. This one-time proof grants no continuing canonical-read
authority. Ignored scratch captures and publisher artifacts remain evidence
that the rejected workflow was exercised; they are not accepted activation or
completion authority.

The current canonical project-relative store declarations are:

| Store | Path |
| --- | --- |
| Market | `data/market.sqlite` |
| Macro | `data/macro.sqlite` |
| Company | `data/company.sqlite` |
| News | `data/news.sqlite` |

A registry path declaration never authorizes opening, creating, migrating, or
writing the corresponding database.

## Bounded manual operational fast path

A current explicit user request may authorize a finite manual provider
workload, use of an existing credential resolver, and writes to an existing
canonical store without creating a new project stage or evidence program.
Before execution, the agent states the resolved provider/dataset, exact store,
date or universe scope, request cap, and retry policy. That single
authorization covers all enumerated units within the stated cap; a separate
approval is not required for each window or symbol.

This lane is available only when the established provider integration,
credential name/resolver, schema, store role/path, locks, and replay-safe
publisher are reused. Responses are bounded and validated before locking;
network work is complete before a write transaction begins; writes are atomic
and use existing relations; and focused preflight plus post-write counts and
integrity checks can establish the result. A private collector binding to
existing datasets is allowed when it adds no migration, dataset ownership,
job, timer, export, public tool, or caller-selected path.

This is task-scoped authority, not a standing provider or store permission. It
does not cover a new provider or credential mechanism, migration, destructive
rewrite, store copy/replacement/promotion/retirement, scheduler, recurring
automation, deployment, public exposure, or an unbounded/materially costly
workload. Those actions retain their applicable heavier gates. It also does
not silently reopen a completed no-repeat population; the current user request
must identify that population if repetition is intended.

## Retired BLS CPI original-release archive candidate

ADR 0011 retires only the proposed `2.22.0` BLS CPI original-release archive:
`macro:0017_bls_cpi_release_archive`, its archive datasets, collector,
handler, and relations. CPI surprises remain FMP-only; archive availability
may not validate, fill, replace, or otherwise affect an output. This does not
retire the completed `2.16.0` BLS annual-revision snapshots, the completed
14-file historical backfill, or the fixed current BLS refresh. No provider,
scheduler, store, or historical-population action follows from the retirement.

## Canonical market boundary

- Stage 12C is complete and is the sole reviewed write to
  `data/market.sqlite`. Its provider work must not be repeated or broadened.
  See the [contract](STAGE12C_MARKET_GAP_V1.md) and
  [evidence](STAGE12C_EVIDENCE.md).
- Stage 12D is complete as a no-transfer, read-only adoption/freeze proof. It
  authorizes no provider, credential, write, copy, backup, promotion, public
  consumer, or scheduler action. See the
  [contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md) and
  [evidence](STAGE12D_EVIDENCE.md).
- Stage 12E remains closed. The presence of market-close service/timer files
  does not authorize linking, starting, enabling, testing, or scheduling them.
- No other default-path market operation is authorized.

## Active recurring exceptions

Exactly three recurring scheduler exceptions are recorded as installed and
active. Their normal clock-driven execution is the boundary; agents must not
manually trigger, change, retry, broaden, reinstall, disable, or repurpose
them.

| Timer | Fixed scope |
| --- | --- |
| `quant-data-macro-vintages.timer` | 09:05 America/New_York on weekdays. One current BEA GDP workbook request and one current BLS GDP/CPI request; no retry, migration, credential, or historical-archive fetch. |
| `quant-data-employment-vintages.timer` | First Friday of each month at 10:05 America/New_York. One credential-free BLS request for the fixed payroll and unemployment series; no retry, migration, or Philadelphia Fed historical-workbook fetch. |
| `quant-data-fmp-macro-calendar.timer` | 08:15 and 08:45 America/New_York on weekdays. One bounded current-window FMP calendar request with no retry. The response is retained as wholesale raw evidence before independent GDP/CPI and employment normalization. |

These exceptions do not enable any recovered Stage 7 job, market-close timer,
new provider, new series, different cadence, catch-up run, or historical
backfill. See [scheduling and locking](SCHEDULING_AND_LOCKING.md) and the
read-only [unit inspection guide](../../deploy/systemd/README.md).

## Completed work that must not be repeated

The following are completed, retained operations, not standing permissions:

- the bounded Stage 9 FMP SPY slice
  ([evidence](STAGE9_EVIDENCE.md));
- the Stage 10 base population and historical extension
  ([evidence](STAGE10_EVIDENCE.md));
- the Stage 11 BEA/EIA macro population
  ([evidence](STAGE11_EVIDENCE.md));
- the Stage 12C two-session market population
  ([evidence](STAGE12C_EVIDENCE.md));
- the one-time GDP/CPI historical archive and initial official-vintage
  population;
- the one-time Philadelphia Fed employment historical-workbook population;
- the one-time macro-history extension and local Stage 11 weekly adoption;
- the one-time FMP GDP/CPI release-calendar history; and
- the one-time FMP wholesale calendar history and its local replay.

The rebuild [index](README.md), root [project record](../../README.md), and
stage evidence own the exact scope, receipt, waiver, hash, count, and historical
projection details. None of the completed work authorizes a repeat, new target,
new date range, provider change, retry, promotion, retirement, public exposure,
or scheduler.

## Closed operations

Outside a current bounded operational authorization or an applicable accepted
heavier gate:

- do not make a live provider request outside the three clock-driven
  exceptions above;
- do not read an existing credential or introduce a credential mechanism for
  an unapproved operation;
- do not install, start, enable, disable, update, or remove a scheduler;
- do not open or write an existing canonical store, or create, migrate, copy,
  move, back up, replace, promote, retire, or destructively inspect one;
- do not expose a public tool, dashboard, export, route, or caller-selected
  database path;
- do not host or deploy Atlas, connect it to operational stores, or run live
  diagnostics;
- do not repeat a completed historical population or locally adopted cohort;
  and
- do not infer authority from a declaration, test, unit file, executable,
  database, environment variable, or historical instruction.

Read-only canonical checks are allowed when the bounded operational scope or
applicable accepted procedure makes them in scope. They must use the
established immutable/fingerprint-preserving procedure; an ordinary SQLite
open can create or update sidecars and is not presumed mutation-free.

## Required document routing

| Work | Additional authority |
| --- | --- |
| Private collector binding to existing datasets | [Fast path](FAST_PATH_DEVELOPMENT.md), [registry specification](SYSTEM_REGISTRY_SPEC.md), and explicit current scope |
| Migration, registry schema/dataset ownership, or shared identity | [Registry specification](SYSTEM_REGISTRY_SPEC.md), [migration reconstruction map](MIGRATION_RECONSTRUCTION.md), applicable ADR, and explicit current scope |
| Provider, credential, canonical path, or lock | [Fast path](FAST_PATH_DEVELOPMENT.md), [scheduling and locking](SCHEDULING_AND_LOCKING.md), and an applicable collector/stage contract only when the heavy-workflow criteria require one |
| Timer or scheduler | [Scheduling and locking](SCHEDULING_AND_LOCKING.md), explicit current authority, and the applicable accepted contract |
| Identity, availability, as-of, or missingness | [Data and time contracts](DATA_AND_TIME_CONTRACTS.md) and `ARCHITECTURE.md` |
| Market default | Stage 12C/12D contracts and evidence linked above |
| Fixed local read-only tool or Inspector | [Fast path](FAST_PATH_DEVELOPMENT.md) and [tool platform](TOOL_PLATFORM_SPEC.md) |
| Writable UI, export, Atlas, deployment, or public route | [Tool platform](TOOL_PLATFORM_SPEC.md), applicable UI/export contract, and explicit public/deployment authority |
| Destructive, promotion, retirement, copy, or backup action | Exact target, recovery evidence, applicable contract, and a new explicit user decision |

If these sources conflict, do not select the permissive interpretation. Stop,
identify the exact conflict, and request the smallest decision needed.
