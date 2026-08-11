# Quant Data Infrastructure Agent Instructions

## Scope and authority

This workspace began as a documentation-only recovery package. On 2026-08-09
the user accepted the Stage 0 planning baseline, authorized a fresh Git
baseline, and authorized the bounded offline Stage 1 vertical slice, Stage 2
four-store foundation, and Stage 3 market/macro fixture restoration. Do not
claim that a documented contract is implemented until its executable evidence
passes the applicable gate, and do not invent historical behavior.

Before cross-cutting work, read [the rebuild documentation index](docs/rebuild/README.md).
Its authority order is:

1. explicit user decisions;
2. accepted ADRs;
3. `ARCHITECTURE.md` and the focused rebuild specifications;
4. `ROADMAP.md`; and
5. `plan.md` as recovery evidence and historical context.

The rebuild documents and ADRs are Accepted as the current target contracts.
The offline Stage 1 vertical slice and Stage 2 four-store foundation are
implemented and fixture-validated; Stage 2 has also passed independent
verification. The bounded offline Stage 3 market/macro scope is implemented
and fixture-validated and has passed independent SolUltra verification. The bounded offline Stage 4 company, news, and options scope and the bounded
offline Stage 5 composable tool platform are fixture-validated and have passed
independent SolUltra verification. The bounded offline Stage 6 local portal is
limited to four fixed routes and was accepted after its offline executable
verification and the user's explicit browser-automation waiver. The bounded
offline Stage 7 manual job rehearsal is implemented, and its primary fixture
gate and independent SolUltra verification have passed. Stage 7
remains manual-first, disabled, and synthetic-fixture-only. The user has also
authorized the bounded offline Stage 8 implementation. Its one declared
fixture-only manual JSON Atlas profile is a deliberate forward reconstruction,
not a claim of recovered historical Atlas or 13-dataset parity. Stage 8 may use
only explicit synthetic-store roots, complete physical locks, SQLite online
backup copies reopened query-only, and an exact derived-output root. It may
atomically promote a fully validated immutable derived revision and its
`current` pointer inside that output root; it may not promote an operational
store. The bounded Stage 8 implementation has passed its primary offline fixture gate.
Independent SolUltra verification also passed; browser verification remains pending, so
the formal Stage 8 exit gate is not closed.

Do not install, start, update, or remove a scheduler; do not start live
providers. Do not host or deploy Atlas, connect it to operational stores,
select default paths, run live diagnostics, or perform destructive operations.

Do not search for lost Git history. The user authorized a fresh repository
baseline; it must not imply recovered history. Do not edit
`sites/quant-data-atlas/.vite/`; it is surviving optimizer-cache metadata, not
recoverable application source.

## WSL-first execution

The canonical project root is:

`/home/volatility/Python_Projects/Quant_Data_Infra`

Use Ubuntu/WSL Linux commands, Linux paths, and Linux-native runtimes for all
project operations. Start commands from the canonical root. Do not mix
Windows-native Python, Node.js, package caches, path syntax, or generated
artifacts into the project.

If the surrounding client exposes a Windows PowerShell host, enter WSL for
project commands with this shape:

`wsl.exe -d Ubuntu --cd /home/volatility/Python_Projects/Quant_Data_Infra bash -lc '<command>'`

Once already inside WSL, run the Linux command directly. Prefer `rg`/`rg
--files` when a Linux installation is available; otherwise use Linux `find`
and `grep`. Use `python3`, not a Windows Python executable.

Discover the executable toolchain before invoking it. The current dependency-
free validation command is
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v`.
The deterministic rebuild CLI requires explicit `--stage`, project, and empty
store/work roots; see `README.md`. Do not invent unavailable package-manager,
framework, or live-provider commands.

## Model routing

Project defaults and named roles are defined under `.codex/`.

- Primary/orchestrator: `gpt-5.6-sol` with `ultra` reasoning.
- Default implementation subagent: `gpt-5.6-terra` with `max` reasoning.
- Named `implementation` agent: TerraMax, write-capable only within an assigned
  scope.
- Named `verifier` agent: SolUltra, independent and read-only; it owns
  adversarial review, acceptance-test analysis, and final verification.

Use the exact requested models and efforts when the runtime supports them. If a
runtime override, account restriction, or unavailable model prevents the
configured route, report the effective fallback instead of silently
substituting it.

Project `.codex/config.toml` is loaded only for a trusted project and normally
at the start of a new session. CLI, composer, or live runtime overrides may
take precedence. After these files change, use a fresh session before relying
on the routing policy.

## Primary/orchestrator responsibilities

The primary is the integration owner. It must:

- translate the current user request into bounded tasks and acceptance gates;
- freeze shared interfaces before parallel writers depend on them;
- assign every write-capable agent an explicit, disjoint path set;
- keep architecture decisions, cross-domain semantics, and shared identifiers
  centralized;
- reconcile agent results and perform shared-file integration itself;
- stop writers before independent verification begins; and
- deliver one evidence-backed result rather than concatenated agent reports.

The primary should delegate when two or more independent workstreams can
materially improve speed or quality. It should not delegate a single coupled
change merely to increase agent or token usage.

## Subagent task contract

Every delegated task must state:

1. one concrete objective;
2. whether the task is read-only or write-capable;
3. the exact owned files or directories for a writer;
4. authoritative specifications and fixed interfaces;
5. required validation and evidence;
6. what the agent must not change; and
7. the condition for stopping and escalating.

Use no more than three concurrent subagents in this project configuration.
Read-heavy exploration, test analysis, triage, and specification review may run
in parallel. Parallel writers require disjoint ownership. Subagents must not
spawn additional agents unless the primary explicitly delegates that authority.

## Write ownership and serialized hotspots

Never assign two active writers overlapping paths. A shared file has one owner
at a time and changes hands only through a sequential handoff.

The primary or one explicitly designated integration owner is the sole writer
for these shared semantic hotspots:

- the canonical machine-readable registry and its schema;
- migration ordering, ownership, immutable migration resources, and checksums;
- shared identity, availability, point-in-time, and semantic no-write
  primitives;
- database path resolution, physical lock identity, and multi-lock ordering;
- shared typed contracts, schema generation, public tool-name inventory, and
  route manifests;
- fixture manifests, golden-output approvals, and generated artifacts;
- shared package exports and root integration files; and
- agent configuration, ADRs, and architecture-wide contracts.

Domain workers may prepare scoped fragments and proposals, but the integration
owner merges canonical declarations. A worker that discovers a required change
outside its ownership must stop and report it instead of editing across the
boundary.

Migration work has an additional hard gate: recovery evidence describes
semantic ordering but does not establish byte-exact DDL or historical
checksums. No agent may allocate, renumber, or claim recovered parity for a
migration until a reviewed store/ordinal/resource/checksum/reconstruction map
exists.

## Parallel execution cycle

Use this cycle for every implementation wave:

1. **Freeze:** confirm the authorized stage, contracts, shared interfaces,
   migration/registry ownership, and agent path ownership.
2. **Fan out:** run independent TerraMax workers on disjoint lanes.
3. **Reconcile:** stop writers, review their reports, and let the primary merge
   shared integration changes.
4. **Verify:** run the SolUltra `verifier` from the original acceptance criteria
   and current workspace, not from worker claims.
5. **Correct:** assign findings to one appropriate writer at a time.
6. **Gate:** rerun verification and record evidence before the next dependency
   stage begins.

Later-stage design, fixture planning, and read-only review may happen early.
Executable implementation must not cross an unmet roadmap exit gate. See
[the parallel execution plan](docs/rebuild/PARALLEL_EXECUTION.md).

## Testing and independent verification

Implementation workers test the behavior they own, but cannot independently
approve their own work. The `verifier` must:

- derive checks from the user request, accepted ADRs, focused contracts, and
  stage exit gate;
- inspect the actual workspace instead of trusting summaries;
- run available safe checks and report exact commands and results;
- test failure behavior, boundary conditions, isolation, idempotence, and
  regressions—not only the happy path;
- distinguish missing test infrastructure from a passing test; and
- return findings ordered by severity with reproducible evidence.

For future executable work, offline tests must use explicit temporary roots,
must not require credentials or network access, and must never fall back to a
default or live SQLite path. Read-only tool and dashboard tests must fingerprint
stores before and after requests to prove zero mutation.

## Quant-data safety invariants

All implementation agents must preserve these invariants:

- four operational ownership boundaries: market, macro, company, and news;
- immutable evidence, versioned canonical facts, and derived research remain
  distinct layers;
- no as-of result may see evidence available after its cutoff;
- exact semantic replay causes zero canonical change;
- date-only source precision is not converted into an invented timestamp;
- callers cannot select database paths, submit SQL, or obtain writable public
  read connections;
- locks are keyed by resolved physical store identity, not logical job name;
- no network work occurs while a database write lock or transaction is held;
- SQLite is authoritative and analytical exports are reproducible derivatives;
  and
- live providers, scheduler installation, promotion, and destructive storage
  operations require the applicable roadmap gate and explicit authorization.

## Stop and escalate

Stop the affected lane and report to the primary if any of the following occurs:

- authoritative documents conflict on a semantic or ownership decision;
- two writers need the same path or shared identifier;
- migration ownership, ordinal, bytes, or checksum status is unresolved;
- a domain change would reinterpret shared point-in-time or missingness rules;
- an offline test would touch a live/default path or require network/secrets;
- a requested action would alter an applied migration or silently update a
  golden result;
- a tool, dashboard, scheduler, or export path would bypass the registered
  read/write boundary; or
- safe completion requires a destructive, external, or materially broader
  action not authorized by the user.

## Agent completion report

Every subagent returns:

- objective and owned scope;
- files changed, or an explicit statement that the task was read-only;
- validation commands and results;
- assumptions and unresolved risks;
- shared changes requested from the integration owner; and
- a clear `ready for integration` or `blocked` conclusion.
