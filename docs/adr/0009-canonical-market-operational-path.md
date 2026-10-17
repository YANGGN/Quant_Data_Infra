# ADR 0009: Canonical Market Operational Path

## Status

Accepted — 2026-08-15

## Context

ADR 0001 established four operational SQLite stores and originally named
`data/market_data.sqlite` as the market default. The retained Stage 10 and
Stage 11 populations were deliberately built under a separate external
candidate root, while a project-local `data/market.sqlite` file now exists but
has not yet passed an operational cutover gate.

The user explicitly selected the project-local path
`/home/volatility/Python_Projects/Quant_Data_Infra/data/market.sqlite` for the
future canonical market store. Registry paths remain relative to an explicitly
supplied project root, so the declared value is `data/market.sqlite`.

## Decision

`data/market.sqlite` is the sole canonical default path for the market store.
`QUANT_MARKET_DB_PATH` remains the reviewed narrowly scoped override.

This decision supersedes only the market default-path field in ADR 0001. The
four-store topology, store ownership, environment-override semantics, physical
path resolution, alias rejection, locking, backup, health, and failure
boundaries remain unchanged. At the time of this market-only decision, the
macro, company, and news defaults remained `data/macro_data.sqlite`,
`data/company_data.sqlite`, and `data/news_data.sqlite`; registry `2.15.0`
later aligned those current basenames under ADR 0001 without changing this
market decision.

`data/market_data.sqlite` is not a fallback, alias, compatibility path, or
second market store. Historical Stage 1 through Stage 11 registry projections
may retain that earlier default when reproducing their exact accepted evidence;
the current Stage 12 registry must use `data/market.sqlite`.

## Non-authorization

Selecting the path does not:

- validate or open the current project-local file;
- copy, rename, move, delete, or retire a SQLite file;
- promote or rewrite the retained Stage 9 through Stage 11 candidates;
- repeat a provider request;
- authorize an incremental collector, public consumer, or scheduler; or
- permit a caller-selected path at a public boundary.

Those actions require their later Stage 12 gates.

## Consequences

- Registry and architecture validation must reject the prior default for the
  current revision.
- The resolved physical lock and backup identity changes with the canonical
  path; no symlink or dual-path compatibility layer is allowed.
- Offline tests must prove default-path neutrality and historical projection
  reproducibility without touching project data.
- A later operational cutover must use a reviewed physical lock, SQLite online
  backup, integrity and ownership reconciliation, immutable receipt, and
  source-mutation neutrality.

## Acceptance evidence

Stage 12A owns the executable acceptance evidence for this decision. Passing
its offline gate does not authorize Stage 12B through Stage 12E.
