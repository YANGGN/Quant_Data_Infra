# Fast Path Development

Status: Accepted default workflow
Accepted: 2026-08-17
Revised: 2026-09-05 — user-approved autonomy, routing, and validation update

## Purpose

Deliver the requested outcome with the smallest coherent change and the evidence
appropriate to its risk. This workflow preserves data-safety and operational
authorization boundaries without turning routine work into a new rebuild stage.

## Authority and context

Ordinary reversible implementation steps within an authorized outcome need no
additional confirmation. Make reasonable choices and explain material assumptions.
Ask only when an unresolved decision would change authorized external effects,
authority, or data semantics, incur material cost beyond approval, or materially
broaden scope. Existing approval persists for its stated scope across turns; a
finite provider authorization never implies more requests, retries, dates,
providers, or recurring execution.

Apply the authority order in [AGENTS.md](../../AGENTS.md). Resolve clearly
superseded guidance without an approval loop, note the precedence briefly, and
continue. An unresolved conflict blocks only the affected action.
Read relevant document sections; reuse unchanged material already read in the
session. Historical evidence is required only when the current decision relies
on it. A dated activation record is not proof of current host state.

## Applicability and lanes

### Local development and additive compatibility

Use this lane for code, tests, documentation, calculations, and bounded local
read-only tools that reuse established interfaces. One integration owner,
focused validation, and final diff review are sufficient unless the actual
change triggers a heavier gate.

A tool added to the existing local manifest is a compatibility change. It is
external exposure only when the work introduces hosting or new network access.
Using established identity, time, or missingness contracts does not itself
require migration reconstruction. Changes to those shared semantics do.

### Bounded operations

Existing providers, credentials, canonical stores, and private collector
bindings may use this lane when all of the following hold:

- the current explicit request authorizes a finite workload, target, and
  intended current or historical result;
- the owner states the exact date/universe scope, request cap, credential
  resolver, and store before execution;
- existing provider, schema, ownership, path, lock, and replay-safe publishing
  mechanisms are reused;
- fetches finish before write locks and transactions;
- writes append, version, or replay-safe upsert; there is no destructive
  rewrite, migration, store replacement, promotion, or retirement;
- no scheduler, recurring automation, deployment, external exposure, or new
  credential mechanism is introduced; and
- focused preflight and bounded post-write validation prove the result.

One authorization covers its enumerated finite units, not hidden retries.
Private collector bindings to existing datasets require integration-owner
registry edits and adjacent checks. New ownership, migrations, or shared
semantics use the heavier lane. Read the [operating envelope](CURRENT_OPERATING_ENVELOPE.md)
and applicable collector/reader contract before operational execution.

## Default workflow

1. Inspect branch, index, dirty files, and uncommitted prerequisites before
   substantial edits. Preserve unrelated work; surface dependencies on it early.
2. State the intended result and select the relevant acceptance checks. For an
   operational task, state its exact finite scope before executing it.
3. Implement through one owner, with optional bounded investigation or disjoint
   implementation lanes when they save time or improve quality.
4. Run the [selected validation](TEST_STRATEGY.md#4-test-layers). Perform only
   authorized operations and their required pre/post checks.
5. Review the final diff. Batch fixes for concrete failures or review findings;
   repeat only affected checks and any newly required adjacent checks.
6. Update the existing documentation that owns changed behavior. Finish when
   the required evidence passes; report limitations and pending supplementary work.
7. Commit and push only when requested or already authorized for this scope.
   Do not silently include unrelated prerequisites in a task commit.

Plan for minutes; 15 minutes is a ceiling, not a target. Do not add phases,
documents, dependencies, abstractions, or rehearsals to compensate for uncertainty.
Reuse verified tool paths within the session and approved existing artifacts.

## Delegation and heavier gates

Read-only investigation and disjoint implementation can use the lightweight
task contract in the [parallel plan](PARALLEL_EXECUTION.md#2-capacity-and-operating-model).
Delegation alone does not require a formal wave or independent certification.
Shared identifiers, registry/schema, migrations, time/lock/path primitives,
generated outputs, and architecture decisions retain one integration owner.

| Actual risk | Required additional gate |
| --- | --- |
| Authorized bounded writes through existing mechanisms | Explicit finite scope, focused preflight, and bounded post-write counts/integrity/lineage |
| Private collector binding to existing datasets | Integration-owner registry edit and adjacent checks |
| Additive bounded local read-only tool | Domain, public-contract, predecessor compatibility, and read-only/cutoff checks from the test strategy |
| Migration, new dataset ownership, shared schema, time/identity/missingness semantics, cross-store or canonical-safety invariant | Applicable accepted contract, impact analysis, full suite where selected by the test strategy, and independent verification |
| New provider/credential/security mechanism, scheduler, deployment, external exposure, writable UI, destructive action, store promotion/replacement/retirement | Explicit authority, operating-envelope review, and the accepted gate for that boundary; independent verification of changed safety guarantees |

A new provider family, credential mechanism, unbounded workload, or materially
costly action cannot borrow authority from the bounded lane. Neither a stronger
model nor a passing fixture expands operational permission.

## Validation and finish

[TEST_STRATEGY.md](TEST_STRATEGY.md#4-test-layers) is the single workflow matrix
for selecting focused, adjacent, or exhaustive checks. A generated-file change
alone is not an exhaustive-test trigger. Specific accepted stage/release gates
and explicit user instructions retain their requirements.

Use existing meaningful checks. Documentation-only changes need link/content
review and diff checks; do not run unrelated executable suites. Add browser
smoke checks when user-visible UI behavior changes.

After the required checks and final review pass, complete the handoff. Expand
testing only for a concrete failure, new change, or unresolved concern. Run slow
supplementary checks in a background/follow-up context that can report their own
outcome, not as a reason to hold the completed implementation open. Required
full-suite checks must finish before the task is claimed complete.

Parallel test processes require proven isolation of temporary roots, stores,
locks, ports, environment, and publication resources. Keep shared-resource tests
serial. Retain original failures and explicitly record successful correction
rechecks; do not inflate coverage with duplicate test IDs or call skips passes.

Report the outcome, changed files, actual checks, deliberately unrun checks, and
material limitations. Stop only the affected unsafe or unauthorized action,
ask once for the smallest missing decision, and continue independent work.
