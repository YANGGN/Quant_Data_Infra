# Quant Data Infrastructure Agent Instructions

## Purpose and authority

This root file is the always-loaded operating map. Detailed history, receipts,
hashes, request rules, and stage-specific evidence live under
`docs/rebuild/`; do not copy them back here. The frozen pre-compression wording
is retained in
[the agent instruction ledger](docs/rebuild/AGENT_INSTRUCTION_LEDGER_2026-08-20.md)
for audit only and grants no authority.

Before cross-cutting work, read
[the rebuild documentation index](docs/rebuild/README.md). The authority order
is:

1. explicit user decisions;
2. accepted ADRs;
3. `ARCHITECTURE.md` and focused rebuild specifications;
4. `ROADMAP.md`; and
5. `plan.md` as recovery evidence and historical context.

Within one authority level, a newer explicitly scoped decision supersedes an
older conflicting one. Record and resolve conflicts; never silently rewrite
history. Do not claim a documented contract is implemented until its required
executable evidence passes, and do not invent historical behavior.

## Mandatory routing

Read the named documents before acting on these triggers:

| Trigger | Required reading |
| --- | --- |
| Ordinary local, reversible development | [Fast path](docs/rebuild/FAST_PATH_DEVELOPMENT.md) |
| Cross-cutting architecture or sequencing | [Rebuild index](docs/rebuild/README.md), applicable ADRs, `ARCHITECTURE.md`, and `ROADMAP.md` |
| Existing provider, credential, network, or canonical-store operation | [Fast path](docs/rebuild/FAST_PATH_DEVELOPMENT.md), [current operating envelope](docs/rebuild/CURRENT_OPERATING_ENVELOPE.md), and [scheduling and locking](docs/rebuild/SCHEDULING_AND_LOCKING.md) only when a writer or lock is involved |
| Private collector binding to existing datasets | [Fast path](docs/rebuild/FAST_PATH_DEVELOPMENT.md), [current operating envelope](docs/rebuild/CURRENT_OPERATING_ENVELOPE.md), and [registry specification](docs/rebuild/SYSTEM_REGISTRY_SPEC.md) |
| Migration, registry schema/dataset ownership, shared identity, point-in-time, or missingness work | [Current operating envelope](docs/rebuild/CURRENT_OPERATING_ENVELOPE.md), [registry specification](docs/rebuild/SYSTEM_REGISTRY_SPEC.md), [migration map](docs/rebuild/MIGRATION_RECONSTRUCTION.md), and [data/time contracts](docs/rebuild/DATA_AND_TIME_CONTRACTS.md) |
| Timer, scheduler, promotion, retirement, deployment, public exposure, or destructive work | [Current operating envelope](docs/rebuild/CURRENT_OPERATING_ENVELOPE.md), [scheduling and locking](docs/rebuild/SCHEDULING_AND_LOCKING.md), and the applicable accepted contract/evidence |
| Fixed local read-only tool or Inspector work | [Fast path](docs/rebuild/FAST_PATH_DEVELOPMENT.md) and [tool platform](docs/rebuild/TOOL_PLATFORM_SPEC.md) |
| Atlas, export, writable UI, deployment, or public-route work | [Current operating envelope](docs/rebuild/CURRENT_OPERATING_ENVELOPE.md), [tool platform](docs/rebuild/TOOL_PLATFORM_SPEC.md), and the applicable UI/export contract |
| Delegation or independent verification | First apply the [fast-path escalation criteria](docs/rebuild/FAST_PATH_DEVELOPMENT.md), then read the [parallel execution plan](docs/rebuild/PARALLEL_EXECUTION.md) |

Code, credentials, unit files, registry declarations, database files, or old
instructions prove only that artifacts exist. They do not authorize execution.
If a required document conflicts with the current envelope or explicit user
scope, stop and report the conflict.

## Always-on boundaries

- Start a live provider operation only when the current explicit user request
  authorizes a finite workload under the bounded operational fast path, or
  through a currently authorized recurring unit listed in the operating
  envelope. One authorization covers the stated finite units; do not add
  hidden retries or broaden their scope. Do not manually trigger, broaden,
  retry, or repurpose recurring units.
- Do not install, start, enable, disable, update, or remove a scheduler without
  a new explicit user decision. Stage 12E and the market-close timer remain
  closed.
- `data/market.sqlite` is the canonical market default. The completed Stage 12C
  slice remains its sole completed reviewed write. A later default-path
  operation requires a current explicit user decision and must qualify for the
  bounded operational fast path or satisfy the applicable heavier gate. Stage
  12D was read-only and had no provider or credential scope.
- Do not silently repeat completed Stage 9-12C or macro/FMP historical
  populations. The current request must identify the exact population and
  finite repeat scope; then apply the bounded operational lane or its
  applicable heavier gate. The operating envelope owns the concise no-repeat
  list; immutable details remain in their evidence records.
- Do not promote or retire a store, expose a public consumer, host or deploy
  Atlas, connect Atlas to operational stores, run live diagnostics, or perform
  a destructive storage action without explicit authority and the applicable
  gate.
- Do not search for lost Git history. The fresh baseline must not imply
  recovered history.
- Do not edit `sites/quant-data-atlas/.vite/`; it is optimizer-cache metadata,
  not recoverable application source.

## WSL-first execution

The canonical project root is:

`/home/volatility/Python_Projects/Quant_Data_Infra`

Use Ubuntu/WSL Linux commands, Linux paths, and Linux-native runtimes for all
project operations. Start commands from the canonical root. Do not mix
Windows-native Python, Node.js, package caches, path syntax, or generated
artifacts into the project.

From a Windows PowerShell host, use:

`wsl.exe -d Ubuntu --cd /home/volatility/Python_Projects/Quant_Data_Infra bash -lc '<command>'`

Once inside WSL, use Linux commands directly. Prefer Linux `rg`/`rg --files`;
if unavailable, use Linux `find` and `grep`. Use `python3`, not Windows Python.

Discover the executable toolchain before invoking it. The dependency-free
validation command is:

`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v`

The deterministic rebuild CLI requires explicit `--stage`, project root, and
empty store/work roots; use the canonical commands in `README.md`. Do not
invent package-manager, framework, or live-provider commands.

## Model routing

Project model defaults and named roles live under `.codex/`; do not duplicate
their versioned settings here. Use the exact configured model and effort when
the runtime supports them. If an override, account restriction, or unavailable
model changes the effective route, report the fallback instead of silently
substituting it. Start a fresh trusted-project session after changing those
files before relying on the new routing policy.

## Proportional engineering

The [fast path](docs/rebuild/FAST_PATH_DEVELOPMENT.md) is the default. Use the
smallest coherent change that follows established patterns, not merely the
fewest changed lines. Do not add speculative abstractions, dependencies,
configuration, compatibility layers, or unrelated refactors.

The fast path includes both a local lane and a bounded operational lane.
Persistent writes, an existing credential resolver, a live request, a
canonical store, or a private collector binding do not alone force the
heavier workflow. They remain fast-path work when the current user request
authorizes a finite workload; existing provider, credential, schema, path,
lock, and replay-safe publisher mechanisms are reused; no migration,
destructive rewrite, scheduler, promotion, deployment, or public exposure is
introduced; and focused preflight plus bounded post-write validation can prove
the result.

For either lane, use one owner, one initial implementation cycle, focused
validation, one final diff review, and a concise handoff. Planning should take
minutes; 15 minutes is a ceiling, not a target. Iterate only in response to a
concrete test failure, review finding, or newly discovered requirement.

Escalate only when an accepted contract requires it or the task changes a
migration, shared schema, dataset ownership, cross-store invariant, security
or credential mechanism, provider family, deployment, scheduler, public
surface, destructive operation, store promotion/replacement, or
canonical-data safety guarantee. A private collector binding to existing
datasets is not by itself an escalation trigger. Two genuinely
independent workstreams may justify delegation when they materially reduce
elapsed time. Add only the gates needed for the identified risk.

## Ownership and delegation

The primary is the integration owner. It centralizes architecture decisions,
cross-domain semantics, shared identifiers, and final shared-file edits. Do
not delegate one coupled change merely to increase agent usage.

Never assign overlapping paths to active writers. A shared file has one owner
at a time. Registry/schema declarations, migration ordering and checksums,
shared identity/time primitives, path and lock semantics, generated contracts,
fixture/golden manifests, shared exports, root integration files, agent
configuration, ADRs, and architecture-wide contracts have one integration
owner.

When the exception workflow is justified, use no more than three concurrent
subagents and follow
[the parallel execution plan](docs/rebuild/PARALLEL_EXECUTION.md). Stop writers
before independent verification. Subagents may not spawn additional agents
unless the primary explicitly delegates that authority.

Migration recovery evidence establishes semantic ordering, not byte-exact DDL
or historical checksums. Do not allocate, renumber, or claim recovered parity
until the reviewed store/ordinal/resource/checksum/reconstruction map exists.

## Validation and evidence

Use a focused completion gate. Run the narrowest meaningful validation first,
expand to adjacent tests only when the changed interface has adjacent
consumers, and add a browser smoke check when user-visible UI behavior changes.

Run the full suite only for a migration, a registry-wide or generated-contract
change, an accepted contract that names it as an exit gate, or an explicit user
request. Do not default to the full suite merely because test selection is
uncertain; inspect the affected interfaces and select focused plus adjacent
checks.

When a full suite is supplementary rather than a required exit gate, do not
hold the interactive implementation handoff open solely for that run. After
the focused gate and final diff review pass, report the implementation complete
and label the exhaustive run separately as pending, running, passed, or failed.
Start a slow exhaustive run only in a background or follow-up execution context
that can report its own outcome, and never imply that a running suite passed.
If the full suite is a required exit gate, it must pass before claiming
completion.

Shard a long suite only when its isolation rules permit it. Do not shard tests
that can interfere through mutable databases, canonical/default paths, locks,
ports, process-global state, or other shared resources. Report what ran, what
was deliberately not run, and whether any exhaustive validation remains.

Routine local changes and bounded operational work through established safety
mechanisms do not need a separate verifier. Independent verification is
required only by an accepted contract or a heavy-workflow condition. A
verifier derives checks from current authority,
inspects the actual workspace, tests failure and boundary behavior, and does
not trust worker summaries.

Offline tests use explicit temporary roots, require no credentials or network,
and never fall back to default/live SQLite paths. Read-only canonical-store
checks must follow the exact approved immutable/read-only procedure; an
ordinary SQLite open is not presumed mutation-free.

## Durable data invariants

- Market, macro, company, and news remain distinct operational ownership
  boundaries.
- Immutable evidence, versioned canonical facts, and derived research remain
  separate layers.
- No as-of result may see evidence available after its cutoff.
- Exact semantic replay causes zero canonical change.
- Date-only source precision is never converted into an invented timestamp.
- Callers cannot choose database paths, submit SQL, or receive writable public
  connections.
- Locks are keyed by resolved physical store identity, not logical job name.
- No network work occurs while a database write lock or transaction is held.
- SQLite is authoritative; analytical exports are reproducible derivatives.

## Stop and report

Stop the affected work and report the smallest decision needed when:

- authoritative documents conflict on scope, semantics, ownership, or current
  operational status;
- a task would cross an unapproved provider, credential, scheduler, canonical
  store, public, destructive, or scope boundary;
- two writers need the same path or shared identifier;
- migration ownership, ordinal, bytes, or checksum status is unresolved;
- an offline check would touch a live/default path or require network/secrets
  outside an explicitly authorized bounded operational scope;
- a requested action would alter an applied migration or silently update a
  golden result; or
- safe completion requires a materially broader action than the user asked
  for.

Escalate once per distinct blocker. Do not invent additional phases, documents,
or rehearsals to compensate for uncertainty.

## Completion report

Lead with the outcome. Name files changed, validation performed and results,
material limitations, and any unresolved risk. Do not claim tests, review,
browser checks, provider outcomes, or guarantees that were not actually
performed.
