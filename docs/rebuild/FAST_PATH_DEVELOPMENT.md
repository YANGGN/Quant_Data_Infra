# Fast Path Development

Status: Accepted default workflow
Accepted: 2026-08-17
Revised: 2026-09-02

## Purpose

Use this fast path for ordinary bounded development and operations. The goal
is to deliver the
requested outcome safely without turning a bounded change into a new project
stage.

The fast path is not permission to skip correctness, safety, or relevant
validation. It chooses the smallest process that can establish them.

## Applicability and lanes

The fast path is the default for all bounded work. Persistent data, an
existing credential path, a live provider, a canonical store, or a minimal
private registry binding does not by itself require a separate stage,
contract, subagent, or independent verifier. Add the smallest gate for the
actual risk instead of switching workflows by category.

### Local fast path

Use the local lane for code, tests, documentation, and other readily
reversible work with no external or persistent effect. One owner, focused
validation, and one final diff review are sufficient.

### Bounded operational fast path

The operational lane may use an existing provider integration, credential
helper, canonical store, and append/upsert publisher when all of these are
true:

- the current explicit user request authorizes the provider, target dataset,
  and intended current or historical outcome;
- the agent states a finite date/universe scope, request cap, and exact target
  before execution; that authorization covers every enumerated unit inside
  the stated cap without a separate approval per unit;
- provider integration, credential name and resolver, store role/path, schema,
  lock discipline, and publisher semantics already exist and are reused;
- input is bounded and validated before the database lock or transaction;
  network work never occurs while either is held;
- writes use existing relations and an atomic, replay-safe append, version, or
  natural-key upsert path; no delete, rewrite, migration, store replacement,
  promotion, or retirement is involved;
- no scheduler, recurring automation, public exposure, deployment, or new
  credential-storage mechanism is introduced;
- focused preflight tests and bounded post-write counts/integrity checks can
  establish correctness; and
- one owner can complete the coupled change.

A collector declaration or reciprocal binding to an existing private dataset
may use this lane when it adds no migration, dataset ownership, public tool,
job, timer, export, or caller-selected path. The integration owner still owns
the registry edit and runs adjacent registry validation.

## Default workflow

1. State the outcome and a short acceptance checklist. For an operational
   task, also state the finite workload, request cap, existing credential
   path, and exact store.
2. Let one owner inspect and perform one initial implementation cycle.
3. Run the smallest focused test or check that proves the change.
4. For an authorized operational task, execute only the stated workload and
   perform bounded post-write count, integrity, and target checks.
5. Review the final diff once for scope, safety, and unrelated edits.
6. Correct only concrete test failures, review findings, or newly discovered
   requirements; batch related corrections into one pass.
7. Update existing documentation once, only if durable behavior, usage,
   contract, or operations changed.
8. Report the result, validation, intentionally unrun checks, and any real
   limitation.

For an ordinary task, planning should take minutes, not hours. Timebox design
and task decomposition to at most 15 minutes; this is a ceiling, not a target.
Planning is sufficient once the acceptance criteria, affected area, and
focused validation are known. If safe implementation still cannot start,
report the exact blocker instead of adding agents, documents, gates, or
rehearsals.

## Keep it small

- Use the primary agent only by default.
- Do not create subagents for one coupled change.
- Do not create a new ADR, stage contract, evidence record, manifest, or golden
  file unless the change actually introduces that kind of durable authority.
  Existing provider, credential, and canonical-store use under the bounded
  operational lane does not by itself require one.
- Do not run the full test suite when focused tests cover a local or bounded
  operational change.
- Do not repeat verification cycles one finding at a time; batch related
  findings into one correction pass.
- Reuse approved databases and artifacts. Do not copy or rebuild a large store
  unless the requested outcome requires it.
- Prefer the smallest coherent change that follows established patterns, not
  merely the fewest changed lines.
- Do not add speculative abstractions, dependencies, configuration,
  compatibility layers, extension points, or unrelated refactors.
- Prefer the nearest existing documentation home. Create a new document only
  when no authoritative home exists or the change creates durable authority
  that requires one.
- Stop when the requested outcome and acceptance checklist are satisfied.

## When to use the heavier workflow

Escalate to the parallel execution and independent-verification workflow only
when an accepted contract requires it or at least one of these conditions is
present:

- a migration, shared schema, dataset ownership boundary, or cross-store
  invariant changes;
- the operation is destructive, irreversible, or difficult to recover;
- a new provider, credential name/resolver/storage mechanism, or unbounded or
  materially costly workload is introduced;
- deployment, scheduling, recurring automation, public exposure, writable UI,
  store promotion, replacement, or retirement is involved;
- two or more genuinely independent workstreams can materially shorten the
  work;
- a security boundary or broad compatibility surface changes; or
- failure could silently corrupt canonical data and the existing transactional
  or replay guarantees are insufficient.

Even then, use only the agents and gates needed for the identified risk. The
[parallel execution plan](PARALLEL_EXECUTION.md) is an exception workflow, not
the default.

Risk-specific minimums are:

| Risk | Minimum added gate |
| --- | --- |
| Existing provider/credential plus bounded writes to existing relations | Explicit current scope, focused preflight, exact target, and bounded post-write counts/integrity |
| Private collector binding to existing datasets | Integration-owner edit and adjacent registry validation |
| Shared interface or compatibility surface | Impact review and adjacent integration validation |
| Migration, new dataset ownership, shared schema, or cross-store invariant | Applicable accepted contract, recovery/rollback analysis, and independent verification |
| New credential/provider/security mechanism, scheduler, deployment, or public action | Current operating-envelope review, explicit authority, and the gate specific to the changed boundary |
| Destructive action or canonical-store safety-boundary change | Exact-target proof, recovery evidence, explicit authority, and independent verification |
| Independent parallel lanes | Disjoint ownership, one integration owner, and one final verification pass |

## Validation rule

Use this validation ladder:

1. Run the narrowest affected unit, lint, type, link, or content check.
2. Add adjacent integration checks only when a changed interface has adjacent
   consumers. Add a browser smoke check when user-visible UI behavior changes.
3. For a bounded operational run, add only exact-target counts, integrity,
   lineage/replay checks relevant to the publisher, and sidecar/fingerprint
   checks when the established store procedure requires them.
4. Run the full suite only for a migration, a registry-wide or
   generated-contract change, an accepted contract that names it as an exit
   gate, or an explicit user request. Resolve uncertain test selection by
   inspecting affected interfaces and adding adjacent checks, not by defaulting
   to the full suite.
5. If a slow full suite is supplementary, finish the interactive implementation
   handoff after the focused gate and final diff review. Report the
   implementation complete, label the exhaustive run separately, and run it
   only in a background or follow-up context that can report its own outcome.
   A full suite that is a required exit gate must pass before completion is
   claimed.
6. Shard a long suite only when its isolation rules permit it. Keep it serial
   when tests can interfere through mutable databases, canonical/default
   paths, locks, ports, process-global state, or other shared resources.
7. State what ran, what was deliberately not run, and whether exhaustive
   validation is pending, running, passed, or failed.

Independent verification is required only when an accepted contract or the
heavy-workflow conditions above require it. Routine local work and bounded
operational work through established safety mechanisms finish after focused
validation, the applicable post-operation checks, and a final diff review.

## Definition of done

The task is done when the requested behavior works, proportionate validation
passes, no unrelated files were changed, safety boundaries remain intact, and
the handoff is concise. Do not keep polishing after the acceptance checklist is
satisfied. Further hardening belongs in a separate explicitly requested task.

If blocked, escalate once per distinct blocker with the smallest decision
needed. A later, different blocker may be reported separately; uncertainty
alone does not justify a new phase or document.
