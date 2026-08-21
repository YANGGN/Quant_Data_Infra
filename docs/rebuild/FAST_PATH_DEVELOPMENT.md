# Fast Path Development

Status: Accepted default workflow
Accepted: 2026-08-17

## Purpose

Use this fast path for ordinary development. The goal is to deliver the
requested outcome safely without turning a bounded change into a new project
stage.

The fast path is not permission to skip correctness, safety, or relevant
validation. It chooses the smallest process that can establish them.

## Eligibility

A task is fast-path eligible only when all of these are true:

- the change is local and readily reversible;
- it does not change persistent data, a shared contract, public behavior, or a
  canonical safety boundary;
- it needs no credential, live provider, deployment, scheduler, external
  write, or destructive action;
- focused validation can establish correctness; and
- one owner can finish it without crossing a serialized semantic hotspot.

If any item is false, name the specific escalation trigger and add only the
gate needed for that risk.

## Default workflow

1. State the outcome and a short acceptance checklist.
2. Let one owner inspect and perform one initial implementation cycle.
3. Run the smallest focused test or check that proves the change.
4. Review the final diff once for scope, safety, and unrelated edits.
5. Correct only concrete test failures, review findings, or newly discovered
   requirements; batch related corrections into one pass.
6. Update existing documentation once, only if durable behavior, usage,
   contract, or operations changed.
7. Report the result, validation, intentionally unrun checks, and any real
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
- Do not run the full test suite when a focused test covers a local change.
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

- a migration, registry, shared schema, or cross-store invariant changes;
- the operation is destructive, irreversible, or difficult to recover;
- live provider access, credentials, deployment, scheduling, or public
  exposure is involved;
- two or more genuinely independent workstreams can materially shorten the
  work;
- a security boundary or broad compatibility surface changes; or
- failure could silently corrupt canonical data.

Even then, use only the agents and gates needed for the identified risk. The
[parallel execution plan](PARALLEL_EXECUTION.md) is an exception workflow, not
the default.

Risk-specific minimums are:

| Risk | Minimum added gate |
| --- | --- |
| Shared interface or compatibility surface | Impact review and adjacent integration validation |
| Migration, registry, persistent data, or cross-store invariant | Applicable accepted contract, recovery/rollback analysis, and independent verification |
| Credential, provider, scheduler, deployment, or public action | Current operating-envelope review and explicit authority |
| Destructive or canonical-data action | Exact-target proof, recovery evidence, explicit authority, and independent verification |
| Independent parallel lanes | Disjoint ownership, one integration owner, and one final verification pass |

## Validation rule

Use this validation ladder:

1. Run the narrowest affected unit, lint, type, link, or content check.
2. Add adjacent integration checks only when a changed interface has adjacent
   consumers.
3. Run the full suite only for plausibly broad regressions, uncertain test
   selection, a required exit gate, or an explicit user request.
4. State what ran and what was deliberately not run.

Independent verification is required only when an accepted contract or the
escalation conditions above require it. Routine local and reversible changes
finish after focused validation and a final diff review.

## Definition of done

The task is done when the requested behavior works, proportionate validation
passes, no unrelated files were changed, safety boundaries remain intact, and
the handoff is concise. Do not keep polishing after the acceptance checklist is
satisfied. Further hardening belongs in a separate explicitly requested task.

If blocked, escalate once per distinct blocker with the smallest decision
needed. A later, different blocker may be reported separately; uncertainty
alone does not justify a new phase or document.
