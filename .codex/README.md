# Project agent routing

Accepted: 2026-09-05 — user-approved quality-first routing and UI design ownership.

[config.toml](config.toml) sets the primary and generic subagent defaults.
Each standalone role below pins its own model, effort, and sandbox. These are
selectable presets, not a pipeline that runs every role for every task.

| Route | Model | Effort | Use |
| --- | --- | --- | --- |
| Primary / integration owner | GPT-6 Astra | xhigh | Shared decisions, consequential implementation, final integration and review |
| Generic subagent fallback | GPT-5.6 Terra | high | Prefer a named role with explicit ownership |
| [implementation](agents/implementation.toml) | GPT-5.6 Terra | medium | Small bounded implementation with settled interfaces |
| [implementation_high](agents/implementation_high.toml) | GPT-5.6 Terra | high | Substantial domain-local implementation with settled shared semantics |
| [mechanical](agents/mechanical.toml) | GPT-5.6 Luna | max | Exact transformations and patterned changes with checkable results |
| [investigator](agents/investigator.toml) | GPT-5.6 Luna | medium | Read-only retrieval, inventories, structured summaries |
| [analyst](agents/analyst.toml) | GPT-5.6 Sol | high | Read-only failure analysis, design challenge, optional focused review |
| [analyst_deep](agents/analyst_deep.toml) | GPT-5.6 Sol | xhigh | Difficult migration, replay, cutoff, locking, or publication analysis |
| [adversarial_tests](agents/adversarial_tests.toml) | GPT-5.6 Sol | high | Explicitly owned failure-path tests and temporary-fixture helpers |
| [verifier](agents/verifier.toml) | GPT-6 Astra | xhigh | Fresh independent verification when required by risk or an accepted gate |
| [ui_design](agents/ui_design.toml) | GPT-6 Astra | xhigh | All UI design judgment, its implementation, and visual review |

## Choosing ownership

The primary directly completes small work when a handoff adds overhead.
Use task-scoped domain workers; market, macro, company, and news do not each
need a permanent agent. Keep one writer by default, normally zero or one
subagent, and no more than three concurrently within runtime capacity.
Parallel writers need disjoint paths. Executors do not delegate recursively.

A compact brief supplies owned files, intended behavior, relevant invariants,
acceptance cases, required checks, and the point requiring escalation.
Pass relevant references or a compact context package; do not routinely fork
the entire conversation and rebuild history. A single command, test invocation,
or search rarely justifies a new agent.

Known risk determines ownership before coding. Shared temporal/identity
semantics, migration integrity, physical locking, replay and canonical
publication guarantees remain with the primary. An unresolved ambiguity or
failed speculative repair returns to the primary with evidence and attempted
approach; avoid repeated speculative fixes on a cheaper model.
Luna Max increases reasoning effort, not its authorized responsibility.

## UI design

Use Astra xhigh for every UI/UX design task: layout, visual hierarchy,
typography, spacing, color, interaction, accessibility, and visual review.
The primary can own this directly when its effective route matches; otherwise
use ui_design. Implementation requiring design judgment uses the same route.
Other implementation roles may make only explicitly specified mechanical UI
changes after the design is settled. User-visible behavior changes retain
applicable browser smoke and visual checks. Design does not grant hosting,
public exposure, or operational-store access.

## Analysis, tests, and independent review

Sol's depth is directed at concrete failure paths and accepted requirements.
Each actionable finding states the trigger, violated requirement/invariant,
evidence, and smallest sufficient correction. Keep optional improvements
separate; infrastructure needs an accepted requirement.

An analyst is read-only; adversarial_tests writes only assigned tests/helpers.
Neither role self-certifies its work. For mandatory independent verification,
use a fresh verifier with the original acceptance criteria and a stable
integrated baseline, after stopping writers. An author of the implementation
or tests cannot independently verify that same work.

Routine work ends with the primary's final review and the relevant checks.
Do not automatically run both analysis and independent review. Required gates
remain in [the test strategy](../docs/rebuild/TEST_STRATEGY.md#4-test-layers).
Batch concrete corrections and rerun only affected checks or newly required
gates. Quality is judged by correctness and the completed task, not token count
or the number of agents, tests, or review cycles.

## Effective configuration

Named role files take precedence for their pinned model and effort. Select
the appropriate preset; do not assume a spawn override changes a pinned role.
The paired implementation and analyst roles provide explicit effort choices.
The primary defaults to xhigh for substantive work; routine follow-ups may use
an explicitly selected medium setting, except UI design keeps its required route.
Max is reserved for the bounded mechanical preset or a specifically justified
hard problem. Ultra is not a default; use it only for substantial work that
benefits from parallel execution. Retain all ownership and concurrency limits.

Start a fresh trusted-project session after configuration edits. The current
task and existing agents may retain their loaded settings. Check effective
model/effort and available roles before relying on the new policy; prose cannot
change the running model. Disclose an unavailable route or override, preserve
independent work, and return the affected routing decision to the primary
instead of silently substituting a model for critical or UI work.

Official format reference: [custom subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents).
