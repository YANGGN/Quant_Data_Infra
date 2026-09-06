# Quant Data Infrastructure Agent Instructions

## Authority and autonomy

Follow explicit user decisions, then accepted ADRs, `ARCHITECTURE.md` and
focused specifications, `ROADMAP.md`, and finally `plan.md` as recovery context.
A newer explicitly scoped decision supersedes an older conflicting one.
Resolve clear precedence and note it briefly; stop only the affected action
when authority or semantics remain unresolved. Continue independent work.

Complete ordinary reversible steps within the authorized outcome without
reconfirming permission. Make reasonable implementation choices and explain
material assumptions. Ask only when an unresolved decision would change
authorized external effects, authority, or data semantics, incur material cost
beyond approval, or materially broaden scope. Approval persists for its stated
scope across turns; it never adds provider units, retries, or duration.

Historical contracts and receipts describe their own decision dates and scope.
Preserve their evidence; do not treat an old milestone restriction as a veto
over a later explicit decision. Do not invent history or claim implementation
without executable evidence. The [instruction ledger](docs/rebuild/AGENT_INSTRUCTION_LEDGER_2026-08-20.md)
is frozen audit context, not operating authority.

## Read only what the task needs

Use relevant sections of the following documents. Reuse material already read
in this session unless it changed or new scope requires it. The
[rebuild index](docs/rebuild/README.md#2-document-map) is navigation; historical
receipts and stage narratives are required only when a decision depends on them.

| Actual change or operation | Read |
| --- | --- |
| Routine local development | [Fast path](docs/rebuild/FAST_PATH_DEVELOPMENT.md) |
| UI/UX design, visual implementation, or visual review | [UI design route](.codex/README.md#ui-design), fast path, and the applicable UI/export contract |
| Additive local read-only tool or Inspector behavior | Fast path and [tool platform](docs/rebuild/TOOL_PLATFORM_SPEC.md); existing time/missingness contracts when used |
| Provider, credential, canonical-store operation, or private collector binding | [Operating envelope](docs/rebuild/CURRENT_OPERATING_ENVELOPE.md), fast path, and the applicable existing collector/reader contract; [locking](docs/rebuild/SCHEDULING_AND_LOCKING.md) for writers or locks |
| Migration, registry schema, dataset ownership, or changes to shared identity/time/missingness semantics | [Registry](docs/rebuild/SYSTEM_REGISTRY_SPEC.md), [data/time contracts](docs/rebuild/DATA_AND_TIME_CONTRACTS.md), applicable ADRs; [migration map](docs/rebuild/MIGRATION_RECONSTRUCTION.md) when migrations or ownership change |
| Architecture or sequencing | Applicable ADRs and sections of `ARCHITECTURE.md` and `ROADMAP.md` |
| Scheduler, promotion, retirement, deployment, external exposure, export, writable UI, or destructive action | Operating envelope, locking where relevant, and applicable UI/export/operational contract |
| Delegation or independent verification | [Parallel plan](docs/rebuild/PARALLEL_EXECUTION.md#2-capacity-and-operating-model), sized to the actual work |

Using an established identity, time, or missingness contract does not itself
require migration reconstruction. Adding a bounded local tool contract follows
the compatibility lane; opening network access or hosting is external exposure.

## Operational boundaries

- The operating envelope is the sole index of recorded operational
  authorization and dated activation evidence. It grants no new authority and
  does not prove current host state. Scheduling mechanics live in the locking
  specification; do not duplicate live-status inventories here.
- Live provider work requires an explicit finite workload in the current
  request or normal clock-driven execution of an authorized listed unit.
  Reuse existing credentials, providers, schema, paths, locks, and replay-safe
  publishers. State the exact scope and request cap; add no hidden retry.
- Without a new explicit user decision, do not manually trigger, retry,
  broaden, repurpose, install, start, enable, disable, update, or remove a
  recurring unit. Do not interfere with its authorized normal clock execution.
- `data/market.sqlite` is the canonical market default. Existing stores are
  accessed only within authorized scope using established readers/publishers.
  Read-only checks follow the approved immutable procedure; an ordinary SQLite
  open is not presumed mutation-free.
- Do not repeat completed populations without a request naming the exact
  population and finite repeat scope. Use the envelope's no-repeat list.
- Store promotion/retirement, destructive storage, Atlas hosting/deployment,
  operational-store connections, live diagnostics, or external exposure need
  explicit authority and their applicable gate.
- Artifacts, credentials, tests, unit files, and registry declarations prove
  existence, not permission to execute.
- Do not search for lost Git history or edit `sites/quant-data-atlas/.vite/`.

## Execution and Git

Use Ubuntu/WSL Linux commands and native runtimes from:
`/home/volatility/Python_Projects/Quant_Data_Infra`.
From PowerShell:
`wsl.exe -d Ubuntu --cd /home/volatility/Python_Projects/Quant_Data_Infra bash -lc '<command>'`.
Do not mix Windows Python/Node, caches, or generated artifacts into the project.

Discover tools once per session and reuse verified paths unless the environment
changes. Prefer Linux `rg`; otherwise use `git grep` or scoped `find`/`grep`.
Keep code searches within source/docs/test paths, not operational data roots.
Use `python3` with `PYTHONDONTWRITEBYTECODE=1`. Canonical rebuild commands in
`README.md` require an explicit stage and empty store/work roots; do not invent
package-manager, framework, rebuild, or live-provider commands.

Before substantial edits, inspect the branch, index, dirty files, and
uncommitted prerequisites. Preserve unrelated work and identify dependencies
on it early. Use isolated worktrees when useful; never silently absorb unrelated
changes. Commit and push only when requested or already authorized for this scope.

## Ownership and completion

One integration owner controls shared registry/schema declarations, migrations,
identifiers, time/path/lock semantics, generated contracts, shared goldens,
exports, root integration files, agent configuration, and architecture decisions.
Never assign overlapping paths to active writers. Models and efforts live in
[the routing configuration](.codex/README.md); select the role for the actual
risk and disclose effective-route differences. Start a fresh trusted session
after configuration edits before relying on new routes.

The configured design owner handles all UI/UX design, visual judgment, and its
implementation. Other workers may make specified mechanical UI edits only
after that design is settled. Keep applicable browser and visual checks.

Keep one writer by default, normally zero or one subagent, and at most three
concurrent subagents within runtime capacity. Use compact complete briefs and
bounded investigation or disjoint work when useful; executors do not recursively
delegate. Return unresolved semantics or failed speculative fixes to the primary.
Direct failure analysis at accepted invariants, concrete evidence, and the
smallest sufficient correction; separate optional improvements from blockers.
Formal verification needs a fresh reviewer who did not author the work or its
tests, a stable integrated baseline, and stopped writers.

Choose the smallest coherent change and relevant checks. Planning takes minutes;
15 minutes is a ceiling, not a target. Avoid speculative abstractions and
unrelated refactors. Batch concrete corrections and review the final diff once.

## Validation

[TEST_STRATEGY.md](docs/rebuild/TEST_STRATEGY.md#4-test-layers) owns the selection
matrix: local fixes use focused checks; additive local tools add compatibility
and boundary checks; migration/shared safety or generator-semantic changes use
the full suite and applicable independent verification. Merely changing
generated output does not by itself require the full suite. Specific accepted
stage, release, and promotion gates and explicit user requests still apply.

Full offline command when required:
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v`.
Offline checks use explicit temporary roots, no credentials/network, and no
default/live SQLite fallback. Parallel tests need proven resource isolation;
shared database, lock, port, or publication work remains serial.

Once the required checks and final review pass, finish. Expand or repeat checks
only for a concrete change, failure, or unresolved concern. Supplementary slow
runs must not hold the handoff open; required full-suite gates must finish.
Report actual results, skipped checks, unresolved risks, and any pending run.

## Durable invariants and stop conditions

- Market, macro, company, and news retain distinct operational ownership.
- Immutable evidence, versioned canonical facts, and derived research stay separate.
- As-of results cannot see future evidence; date-only precision stays date-only.
- Exact semantic replay causes zero canonical change.
- Callers cannot choose database paths, submit SQL, or obtain writable connections.
- Locks use resolved physical store identity; network work precedes write locks/transactions.
- SQLite is authoritative; analytical exports are reproducible derivatives.

Stop the affected action for unresolved authority, ownership, migration
ordinal/bytes/checksum, unsafe live/default access, unapproved external effects,
destructive scope, an applied-migration edit, or a silent golden change.
Escalate once per blocker with the smallest missing decision. Preserve completed
work and continue safe independent work; do not invent extra phases or rehearsals.

Lead the handoff with the outcome, changed files, actual validation and material
limitations. Do not claim browser, provider, test, or safety evidence not obtained.
