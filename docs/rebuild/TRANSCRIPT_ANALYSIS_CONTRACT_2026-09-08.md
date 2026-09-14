# Transcript analysis contract — 2026-09-08

## Current reader workflow — structured call records, 2026-09-12

The user approved the structured mock-up and requested implementation plus one
repeat live test for the same ADM capture. New private paired preparations now
default to structured, using transcript_structured_call_prompt.v1 and
[structured schema](TRANSCRIPT_STRUCTURED_CALL_V1.schema.json) with its
[editor review schema](TRANSCRIPT_STRUCTURED_CALL_REVIEW_V1.schema.json).
The earlier call_brief and detailed profiles remain explicit options; frozen
plans and request bytes retain their historical meaning.

The reader gets a one-line headline and six fixed table sections: reported
results (six rows maximum), guidance (six), business drivers (four), selected
analyst questions/answers (three), watch items (three), and management tone
(one). No section has a minimum row quota. Narrative fields are single-line
text, capped at 30 words, with 25 for the headline and 45 for analyst answers.
The complete rendered document retains the approximately 550-word target and
700-word hard ceiling, scaled down for shorter sources by the prior formula.

Point/range values use exact decimal strings plus explicit units. Numeric
qualifiers preserve approximate figures and directional bounds; unused fields
remain null. Unknown periods/comparisons remain null, previous guidance may
be unavailable, and missing values never become zero. The validator checks
value shape, bound ordering, consistent units, and whether explicit guidance
change labels agree with supplied comparable numbers. Call identity is copied
from the trusted source rather than invented by the model.

Sol/xhigh writes the structured record from the complete source. Astra/xhigh
checks the complete source and returns the corrected structured record.
Reference, materiality, ambiguity and editorial rules from the concise profile
continue to apply. The reviewer may repair a schema-shaped invalid draft within
the two-call workload; malformed drafts stop. The original draft and reviewer
output are retained. A failed final validation, token bound, unresolved issue
or native metadata fallback prevents validated_private status.

The new requested run is exactly one ADM source-labelled 2026 Q2 capture,
equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91, maximum two model
requests, 1,200 seconds per request, 32,768 output tokens per completion,
ChatGPT subscription transport only, no automatic retry or API fallback.
This is a new explicit finite repeat authorization; earlier consumed pilots
are not reused as authority. It includes a quiet immutable pre/post check of
the retained capture and analysis tables, and private artifacts only. No
Equibles refetch, migration, canonical publication or recurring unit changes
are part of this run. Evidence is under
.local/transcript-structured-live-20260912/.

## Earlier concise call-brief workflow, 2026-09-12

The user's September 12 decision supersedes exhaustive extraction as the reader
document: the output must capture the essence of the call in a highly informative,
concise brief. New preparations in the private paired pilot default to
call_brief. The older detailed format and frozen plans remain replayable with
their original request bytes and rules. Historical contracts and dated receipts
below describe their own versions; they do not require a new brief to reproduce
an exhaustive claim ledger.

The new private profile is transcript_call_brief_prompt.v1, with
[brief schema](TRANSCRIPT_CALL_BRIEF_V1.schema.json) and
[editorial review schema](TRANSCRIPT_CALL_BRIEF_REVIEW_V1.schema.json).
It does not change migration 0011, the canonical analysis schema, existing
published records, scheduler units, or production activation.

1. Sol (gpt-5.6-sol, xhigh) reads the complete source and writes a short brief:
   central takeaway, key results/developments, material guidance, selected analyst
   concerns and answers, watch items, and a brief assessment of management tone.
2. Local checks enforce fixed fields, bounded sections, source roles, valid Q&A
   exchanges, duplicate assertion rejection and document length. A schema-shaped
   draft with a word/reference error can use the already planned editorial pass.
   Malformed output stops the pair.
3. Astra (gpt-6-astra, xhigh) independently reads the full source and draft,
   then returns the corrected brief. It fixes important errors and omissions
   while enforcing concision. It does not demand every candidate or question.
   Approved means identical draft; revised means changed and satisfactory;
   needs_attention carries a material unresolved issue.
4. Local checks validate the final brief, decision, usage and native runtime
   warnings. Save the original draft, final candidate, readable Markdown and
   separate audit metadata. No automatic third call or provider fallback occurs.

### Reader length and information selection

The normal target is about 550 words with a hard 700-word ceiling. Shorter sources
scale down: maximum = min(700, source_words, max(100, floor(source_words * 0.15))).
Target = min(550, floor(maximum * 0.8)). Source words are whitespace tokens; reader
words are rendered whitespace tokens containing a letter or digit, including
headings, labels and source IDs, excluding standalone Markdown punctuation.
No minimum content quota applies. Empty sections disappear. Overlong output is
rejected for editing, never silently cut off. The 32,768-token native safety
allowance includes reasoning and is separate from the reader word ceiling.

At most four developments, six guidance rows, three selected analyst topics,
three watch items and one 35-word tone paragraph fit within the global ceiling.
Keep important actuals, guidance changes, units, periods, basis, drivers and
qualifications. Merge repetition. Retain uncertainty only where it changes
interpretation. Do not infer fiscal labels, annual growth, company forecasts
from analyst premises, or totals from potentially overlapping savings.

Evidence uses compact turn IDs instead of repeated evidence summaries. The full
source and raw responses remain retained. Q&A references use the top-level
courtesy_only.v2 inventory generated from source turns; embedded historical
inventory hints do not override it. Non-answer claims cannot omit an existing
management response. Selection does not imply complete question coverage or
a frequency ranking.

### Validation and operational scope

This is a private output-contract addition. Existing detailed pair configurations
and request hashes must remain unchanged. The standard canonical/manual job
still uses its historical schema until a separately authorized compatible storage
change is made. The private pilot's new default does not automatically execute
or replace any stored transcript extraction.

Deterministic checks prove shape, length, references and replay behavior; they
cannot prove factual entailment, material recall or editorial quality. A live
full-source editor evaluation is still required before claiming model quality.
Native fallback metadata warnings prevent a new brief pair from being marked
validated_private, even if the JSON itself passes.

This redesign uses saved ADM source evidence and offline temporary-store tests.
Its ADM example is explicitly an offline editorial example, not a new model
result. It makes zero live calls and zero canonical writes. The previous
two-call Sol/Astra authorization was consumed by the earlier pilot; this code
change does not add provider units or retries.

Prompt length/style requirements follow the official
[OpenAI prompting guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra).
The profile supplies an explicit concise output contract and fixed Structured
Outputs schemas; it does not change the model routing selected for this project.

## Historical detailed analysis contracts and activation evidence

The following sections retain their original version-specific scope.

Status: the bounded manual pipeline and company migration 0011 are active.
New prompt v5 preparations use analysis/review schema v3, a 32,768-token output
allowance, an authoritative versioned question inventory and explicit reference/metric
reconciliation. Source references and numeric checks remain enforced. Legacy requests retain their original rules; the prior
rejected live drafts remain unpublished. Dated evidence is recorded below.

## Scope and model routing

The user selected one transcript pass covering forward management guidance,
the questions/concerns most emphasized by participating street analysts, and
overall management sentiment and expressed confidence. This expands the earlier
guidance-only proposal. The user selected **GPT-5.6 Terra for extraction and
GPT-5.6 Sol for pilot review**, superseding the proposed Sol/Astra combination.

Use `gpt-5.6-terra` with `high` reasoning for extraction and `gpt-5.6-sol`
with `high` reasoning for pilot review as the initial effort defaults. These
are workload settings, not changes to project-wide implementation or UI roles.
The supplied input contains the complete stored transcript, including prepared
remarks and Q&A, subject to an explicit input/output bound. Process every turn
or mark the result partial; do not silently drop text to fit a limit. If chunks
are needed, reconcile all chunks before computing call-wide conclusions.

Use strict Structured Outputs with
[TRANSCRIPT_ANALYSIS_V3.schema.json](TRANSCRIPT_ANALYSIS_V3.schema.json).
Every object has fixed fields and rejects extra properties. Unknown values
remain explicit null/unspecified/insufficient-evidence states. The
[synthetic example](TRANSCRIPT_ANALYSIS_V3.example.json) includes input turns,
model output and separately computed analyst-focus fields. It is not real
company data or evidence of model extraction quality.

The schema is the model-output contract, not the full storage envelope. Code
attaches model configuration, trusted IDs and timestamps, source hashes,
actual processing coverage, usage and review decisions. Transcript content is
untrusted source material, never instructions to run tools or alter the schema.
No browsing, arbitrary tools or direct SQL are needed by the extraction worker.

## Forward guidance

`guidance_claims` retains metric, source wording, company/segment/product/region
scope, target fiscal period, accounting basis, level/growth measurement,
comparison period, growth basis, value shape/unit/scale, management-stated
update action, conditions, source-linked evidence summaries and ambiguities.

One claim has one metric, scope and target period. Include guidance in Q&A.
Separate management statements from analyst hypotheses, historical actuals,
consensus expectations and general aspirations. A missing numeric forecast is
not zero; an explicit withdrawal or refusal to guide is its own assertion.
Do not infer that an expectation is newly issued just because it appears in
this call. Keep vague phrases such as "low single digits" qualitative.

Values use decimal strings; numeric normalization uses Decimal and an explicit
unit policy. Validate point/range/bound field combinations, low <= high, positive
scale and required source evidence. Null accounting basis, currency or target
resolution must not be filled from a ticker or from the call's fiscal quarter.
Resolve calendar target dates outside the model with reviewed fiscal-calendar
metadata, retaining the original period wording. Validate currencies when set.

## Structured coverage and reconciliation

Before finalizing claims, Terra inventories candidate forecasts, operating plans,
assumptions, qualifications and explicit refusals from all management turns.
The required guidance_coverage object lists every reviewed management turn and
each candidate's source IDs, concise summary, kind and disposition. An included
or merged candidate links to one or more claim IDs; an excluded candidate has
no claim links and a nonempty exclusion reason. Every final claim is accounted
for by at least one candidate.

Repeated mentions enrich one claim where metric, scope and horizon agree.
The linked claims retain all candidate source turns in their evidence, including
later assumptions, timing and caveats. Distinct metrics/scopes/horizons remain
separate. Numerical fields retain the existing typed value schema: shape,
decimal-string point/range/bound, unit, scale, currency and comparison basis.

Each analyst topic includes guidance_candidate_ids for forward-looking response
content, including explained exclusions where relevant. References must connect
to a management answer in that topic's question blocks; included/merged Q&A
candidates must appear in a relevant topic. Code checks the ledger and computes
coverage counts. These checks prove structural accounting, not semantic recall:
Sol still compares the full source against candidates, claims and response
summaries to detect omitted or misinterpreted statements.

The [review schema](TRANSCRIPT_REVIEW_V3.schema.json) distinguishes missing_claim,
missing_qualification, interpretation_error, incorrect_number, coverage_gap,
source_ambiguity, acceptable_exclusion and other findings. Missing guidance or
qualification findings identify missing information and propose a correction;
missing qualifications name affected existing claims. An acceptable exclusion
is advisory and cannot itself produce an error verdict.

## Analyst questions and concerns

`analyst_focus.question_blocks` describes every observed analyst question or
follow-up block. A block is a contiguous analyst speaking contribution before
management responds; adjacent turns from that same speaker can form one block.
The conservative versioned courtesy rule (v2 for new v5 requests; v1 for
legacy requests) excludes contributions composed only of
a fixed set of acknowledgements (for example, "Great, thanks.") and punctuation.
A contribution containing a substantive question remains eligible. All source
turns stay intact; courtesy contributions still terminate the preceding exchange,
so a subsequent "You're welcome" is not attributed to the earlier answer. The
trusted source inventory supplies exact eligible block and answer IDs.
Multiple topics in one block do not multiply its identity. Preserve all input
turn IDs and the answer turn IDs. Speaker identity and role come from the
trusted input mapping; ambiguous roles or identities are explicitly unresolved.
Operators and management questions do not count as analyst participation.

`analyst_focus.topics` groups these blocks by their underlying subject. Each
contains a code, label, concise summary, nullable underlying concern, question
references, management response summary, response-status assessment and source-linked
question/answer evidence summaries. Questions may be concerns, clarifications or positive
probes; do not force every question into a negative framing. Topic coding can
use `other` while retaining source-specific labels. All observed blocks remain
available even when the UI displays only the top five topics.

The application computes, after reference validation:

- total distinct question blocks and identified analyst speakers;
- each topic's distinct question-block count;
- each topic's distinct identified analyst count, plus unresolved-speaker count;
- question-block share = distinct topic blocks / all distinct analyst blocks;
- rank by question-block count descending, then distinct identified analysts
  descending, with tied ranks retained and first source appearance as display
  order only.

This makes "most focused" mean most frequently raised in this call. A question
block can cover multiple topics, so topic shares may sum above 100%. Unresolved
speakers do not become fabricated distinct analysts; identified-analyst counts
are lower bounds when identity coverage is incomplete. No-Q&A calls have no
ranked topics and null shares. Partial calls expose partial frequency counts
and cannot assert a complete call-wide top concern. Model-generated counts or
percentages in prose require the same computed reconciliation before display.
These are the participating analysts' questions, not a market-wide consensus.

## Management tone

`management_tone` is explicitly based on **transcript wording**. It cannot
measure vocal delivery, pauses, body language, hidden beliefs or honesty.

Store assessments for `overall`, `prepared_remarks`, `qa`, and each resolved
management speaker when evidence supports it. Each assessment contains:

- sentiment: positive, neutral, negative, mixed or insufficient evidence;
- expressed confidence: high, moderate, low, mixed or insufficient evidence;
- hedging: high, moderate, low, mixed or insufficient evidence;
- assessment support: clear, limited or insufficient;
- concise explanation, supporting evidence summaries, counterevidence and ambiguities.

Sentiment describes favorable/unfavorable business language; confidence describes
how firmly management expresses its outlook. High expressed confidence can
coexist with a negative outlook. Assessment support describes the strength of
our textual interpretation and is not management confidence or a calibrated
probability. Do not invent a 0-100 sentiment or confidence score.

Rubric anchors: high confidence requires direct commitment/conviction language;
low confidence requires expressed limited visibility/uncertainty; moderate
reflects qualified conviction; mixed preserves material differences across
statements or speakers. Routine forecast vocabulary alone is insufficient.
Sentiment must be grounded in the outlook language, not inferred from a
speaker being polite. Hedge labels follow substantive conditions and caveats,
not the standard forward-looking-statement disclaimer read by the operator.

Do not let the length of prepared remarks erase a different Q&A tone. Overall
is an evidence-supported synthesis, not a word-count average. Preserve CEO/CFO
or segment-leader disagreements in speaker assessments and counterevidence.
A missing section has insufficient-evidence assessments, not neutral ones.
No prior-call change score is produced without a separately supplied comparable
prior transcript and a defined comparison policy.

## Evidence, validation and review

New evidence entries use evidence_summary and supplied turn IDs. Paraphrases
are permitted and displayed as evidence summaries, without quotation marks.
Code validates nonempty summaries, source roles and relevant exchanges, then
attaches capture ID, raw page index, original JSON pointer and page hash.
Summary character offsets are null. A separately labelled source_span identifies
the exact complete source turn (kind=turn, start=0, end=len(source text)); it does
not assert a verbatim match to the summary. This preserves exact source lineage.
Legacy v1 evidence retains exact quotation matching and its quotation offsets.
The model cannot manufacture source URLs, issuer mappings, capture times or
database IDs. Guidance and tone reference management; analyst-topic questions
reference analyst turns.

Semantic validation complements schema validation: local IDs must be unique,
references must exist, topics must cite relevant blocks, answers must match
source exchanges, every substantive assertion needs evidence, and question
counts/ranks are recomputed. Valid source references establish traceability;
they do not establish that a summary preserves meaning or that every claim was
found. Sol reviews substantive omissions, interpretation and numeric accuracy.
Refusals, truncation, malformed output, missing sections and an analysis finding
no guidance are distinct outcomes. Preserve unsuccessful attempt receipts.

Sol reviews full pilot transcripts as well as Terra output so it can find
omitted guidance, missing question themes and biased tone summaries. It returns
field/item-specific findings with evidence and proposed corrections. Retain the
original Terra result and the Sol review. Reviewer agreement is model-reviewed,
not human verification or proof of truth. Manually reviewed pilot examples
establish the reference set; unresolved disputes remain flagged.

Measure guidance precision/recall and period/unit/basis accuracy, question
coverage/topic grouping and ranking, tone agreement/evidence support, and
actual token usage. Include no-guidance/no-Q&A cases, qualitative/withdrawn
outlooks, follow-ups, mixed speakers and prepared-versus-Q&A differences.
The pilot workload and spend cap must be explicit before paid calls. There is
no automatic escalation to Astra or a permanent Sol review of every call.

## Implemented storage mapping

Registry 2.75.0 allocates company migration 0011 and these six append-only tables.
All rows belong to company-owned analysis, separate from reported facts.

| Table | Meaning |
| --- | --- |
| `company_transcript_analyses` | Immutable complete extraction response, input/configuration fingerprints, processing coverage, source capture, model/effort, prompt/schema/parser versions, usage and lineage. |
| `company_guidance_claims` | Typed guidance assertions linked to an analysis, with unresolved fields and evidence. |
| `company_analyst_question_blocks` | Source-grounded question blocks, speaker references and answer links. |
| `company_analyst_topics` | Topic summaries, member question IDs, response assessments and evidence; frequency/rank derives from membership. |
| `company_management_tone_assessments` | Overall, prepared, Q&A and per-speaker assessments linked to their source evidence. |
| `company_transcript_analysis_reviews` | Append-only validation/reviewer decisions identifying the analysis and affected output items, with correction links. |

The publisher validates child references and evidence membership. Source spans
share one representation and can initially be retained as structured JSON on
the relevant analysis items; a separate evidence relation is an implementation
choice, not a second source of authority. The original response remains intact.

Reuse `company_equibles_transcripts` and `company_equibles_transcript_pages` as
source evidence. The current `company_guidance_versions` key omits explicit
scope/basis dimensions, so do not force these new results into it. The derived dataset retains the frozen instrument association on the source
capture and the full scope/basis/period fields on each claim. It does not create
an issuer mapping or promote these claims into reported guidance facts. No model-derived tone or
analyst-concern assessment becomes a reported financial fact.

Retain call date as source metadata, local transcript capture as evidence
availability, and extraction/review creation times separately. A reconstruction
of an old call is retrospective and cannot be backdated into point-in-time
research. Cache by input hash plus model/effort and prompt/schema/parser versions;
replay identical retained output without another model call or canonical write.
A later explicit re-extraction keeps its original output and lineage; it does
not overwrite a prior claim or consume an implicit retry budget.

## Manual operation and bounds

The entry point is `quant_data.operations.transcript_analysis`. It uses the
project's fixed company store and established immutable reads and physical store
locks. Help is inert; `prepare` reads retained transcripts and writes an immutable
private plan without resolving credentials or contacting a model.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis --help
# Run only when company schema activation is authorized:
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis apply-schema
# Example finite pilot selection: up to four latest stored AAPL captures.
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis prepare --symbol AAPL --limit 4 --backend openai_api --max-usd 10 --review-pilot
# Paid execution requires approval for this plan's finite workload and budget:
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis execute --plan-id <returned-plan-id>
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis show --analysis-id <returned-analysis-id> --as-of <UTC-timestamp>
```

`apply-schema` checks the exact company predecessor ledger, adds only migration
0011, and registers its derived dataset; it cannot fill missing older migrations.
It can finish registration after an interrupted activation. The market, macro
and news stores are not migrated. Execution checks migration and dataset
identity before it can resolve `OPENAI_API_KEY` through the existing credential
resolver. The model API key is separate from `EQUIBLES_API_KEY`.

Each plan selects 1–10 stored captures for one ticker and permits one Terra call
plus one optional Sol call per capture, at most 20 requests and 3,600 seconds per
invocation. A complete serialized model input, including schema/prompt overhead,
is bounded by 200,000 bytes with a 1,024-byte safety allowance. Output is capped at
32,768 tokens including reasoning for new v5 requests; v1-v4 retain 16,384.
Each response remains bounded at 4 MiB, and each HTTP attempt
at 180 seconds. Oversized input and incomplete output stop without truncation or
automatic retries. A review whose full input exceeds the same bound stops while
retaining the completed Terra analysis; chunking is not implemented in v1.

USD reservations use pinned standard rates from 2026-09-08: Terra $2/$12 and
Sol $4/$20 per million input/output tokens. Conservatively reserving one input
token per serialized byte gives $0.596608 per extraction and $1.12768 per review.
Four paired calls reserve $6.897152. The dollar estimate is tied to those rates,
not a provider billing guarantee; request and token limits are explicit. Actual
reported tokens and estimated usage costs are retained separately.

Private plans, attempt receipts, reserved budgets, raw responses and run reports
live under `data/.operations/transcript-analysis/`. Before a new request, credential-only preflight rejects a missing or invalid key
without reserving an attempt. Before HTTP, execution durably reserves the
request and records a pending attempt. A failed/uncertain attempt
cannot be retried by rerunning or creating a second plan. A received response can
be republished after a local failure without another paid call. Each local
publication attempt has a fresh ingestion-run ID; failed attempts remain in the
ledger while the request and analysis semantic IDs stay fixed. An already
published request is reused without canonical writes. No automatic correction,
model escalation or re-extraction path is enabled.

Section assignment uses the first source-labelled analyst or operator Q&A
transition. Speaker roles are conservatively normalized from retained provider
labels and propagated only for an unambiguous named speaker. Parser
`equibles_turns.v2` recognizes unidentified-speaker placeholders such as
"Unknown Analyst" and "Unidentified Analyst #2" as unresolved IDs, preserving
their original labels. The reader exposes source classification warnings and
the trusted question inventory separately from complete text processing. Unknown boundaries
remain unknown, missing roles are not inferred from company knowledge, and tone
for unresolved sections is insufficient evidence. Frequencies cover identified
source analyst blocks; unresolved roles are disclosed in source warnings.
Currency validation enforces uppercase three-letter formatting when present;
unknown currency remains null. Legacy quotations resolve to Unicode character offsets in
the original decoded turn, with a raw-page hash and JSON pointer.

The six tables and source availability checks are exercised in temporary company
stores. A paid pilot is still necessary to measure extraction quality on actual
Equibles speaker labels, guidance recall and tone agreement; synthetic tests do
not establish those results. The final implementation receipt records completed
test and independent-review evidence.

## References

- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [Raw transcript contract](EQUIBLES_TRANSCRIPT_BACKFILL_2026-09-07.md)
- [Terra model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [Sol model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)


## Codex subscription adapter and ADM pilot — 2026-09-09 UTC

The user's later decision authorizes a Codex subscription adapter and an actual
run for the company whose transcript downloads just finished. The bounded pilot
selects **ADM's 26 complete retained calls**, in latest-first batches of 10, 10
and 6, with one Terra/high extraction and one Sol/high review per capture:
**at most 52 model runs total**, no automatic retries and no API-key fallback.
Activating the already reviewed company 0011 through `apply-schema` is included
in this requested run. No new migration bytes, registry version, Equibles fetch
or analysis recurring unit is introduced.

`prepare` now defaults to `--backend codex_subscription` in the CLI. It accepts
a bounded `--offset` for disjoint batches while each plan still selects 1–10
captures and admits at most 20 runs. Explicit `openai_api` plans retain the v1
contract, pinned prices and existing identities. Codex uses v2 immutable plans,
binds its backend/runtime configuration into request identity, and rejects USD
budgets because subscription allowance is not an API-dollar budget.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis prepare --symbol ADM --limit 10 --offset 0 --backend codex_subscription --review-pilot
# Execute only an approved returned plan ID:
PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis execute --plan-id <returned-plan-id>
```

The fixed native WSL Codex CLI is version 0.144.4 under the existing standalone
installation. Authentication reuses the existing WSL ChatGPT login. The adapter
passes no API keys, access-token overrides or alternate endpoint environment
variables, forces ChatGPT authentication and uses a retry-free custom provider
with Codex's default authenticated endpoint selection. It does not load user
configuration; ephemeral isolated input directories supply only the transcript,
schema and fixed instructions. Shell, apps, plugins, hooks, browsing, delegation
and other optional capabilities are disabled. A native localhost fixture verified
an empty tool list, exact Terra/high configuration, structured output and exactly
one request on both success and HTTP 503.

The adapter records original CLI JSONL, diagnostics, execution outcome and a
normalized response envelope identified as `codex_exec.v1`. Its model identity
comes from the pinned CLI configuration, not a fabricated provider HTTP model
field. Canonical raw-response storage also retains the original JSONL bytes.
Citation, role, question-coverage and review validation still precede publication;
the models never obtain writable database access.

Each run has a 180-second process-group deadline, 3 MiB combined stream bound and
4 MiB normalized response bound. The 200,000-byte serialized input admission bound
includes the supplied prompt/schema, but Codex adds its own protocol context.
The CLI does not expose a provider-side output-token cap: **16,384 output tokens
is a post-completion validation limit**, not a guarantee on subscription usage.
Actual reported input/output/cached/reasoning tokens are retained. Dollar
reservation and estimated API-cost fields are null for subscription execution.
Limit/authentication/transport failures retain their attempt and pause; rerunning
or preparing a new plan cannot silently retry a failed or uncertain request.

The registry's existing `OPENAI_API_KEY` configuration declaration describes the
retained direct API option. This later explicit decision selects Codex's existing
host login through the private adapter; it does not make that API key mandatory
for a subscription plan. Existing dataset ownership, schema, locks, source-time
semantics and exact API-plan replay remain unchanged.

Evidence for implementation, runtime fixtures, finite admission and the resulting
pilot is retained under `.local/transcript-codex-pilot-20260909/`.


### Activation and first live attempt

Company migration 0011 was applied at `2026-09-09T02:05:45.715315Z` using
the existing publisher/physical-lock path. Its reviewed SHA-256, the previous
ten migration rows and all prior company data were preserved. The six analysis
tables were empty on activation; the only prior-table count changes were one
migration, one dataset registration and one identity registration.

The first live call was ADM fiscal 2026 Q2 with Terra/high. At
`2026-09-09T02:10:52.707391Z` the pilot stopped after **one model attempt**
reached the 180-second deadline (measured 180.1 seconds; process exit -9).
Native JSONL contains `thread.started` and `turn.started`, with no returned
message or completion usage. A model-cache diagnostic reported a missing
`base_instructions` field; its causal relationship to the timeout is unproven.
The process group was terminated and no matching worker remained during audit.
No quota-exhaustion or successful model-response claim is supported.

**Zero of 26 ADM calls were analyzed, and no Sol review ran.** No normalized
response or structured analysis was published. The failed/uncertain attempt,
raw event stream and diagnostics are retained; no retry or API fallback ran.
Subscription usage is unknown because the CLI did not return token usage.
The remaining 51 planned model runs were not started. A longer deadline or
controlled retry requires a newly scoped decision; these failed attempt
receipts must not be removed or bypassed.

The immutable completion audit confirmed all 251 transcript page hashes/bytes
and all 26 ADM source-input hashes are preserved, all six analysis tables remain
empty, their foreign-key checks pass and the migration ledger is unchanged
since activation. Canonical replay of live output is inapplicable because none
was published. Offline coverage passed **56 focused tests** (42 existing plus
14 Codex cases), two native CLI localhost protocol fixtures and a fresh
independent review after the process-cleanup correction. The focused binding
gate did not require another full offline suite. Live extraction quality and
end-to-end subscription publication remain unverified.

Private evidence: `schema-activation.json`, `pilot-result.json`,
`completion-audit.json`, `implementation-validation.json` and
`independent-review.json` under the evidence directory above.


### Authorized ADM retry — 2026-09-09 UTC

The user explicitly approved one retry of the failed ADM fiscal 2026 Q2
extraction with a **1,200-second (20-minute) deadline**, retaining Terra and
**high** reasoning. This adds exactly one model attempt to the prior pilot
allowance (53 total including the original failed attempt and the 51 still
unattempted planned calls), with no further automatic retries or API fallback.
The longer bound is per retry transport; the ordinary default remains 180
seconds and fixed prompts, schema, request identity and model settings do not
change. Original failed attempt receipts and native events remain untouched.

The controlled one-time runner holds the existing analysis job lock, records
a durable one-attempt admission in
`.local/transcript-codex-retry-20260909/attempt.json`, and uses a separate
native evidence directory for attempt two. A successful returned response is
retained before the established validation/publisher, using actual retry
timestamps. Publication reuses the original semantic request identity so the
original plans can reuse it without another extraction. There is no deletion
of failed evidence or reset of the original plan's charged request.


### Result of the authorized 20-minute retry

The single retry started at `2026-09-09T02:23:30.803541Z`. Native Codex
completed successfully in **196.87 seconds** with Terra/high and a 1,200-second
deadline, returning a structured draft. Reported usage was 20,659 input tokens
and 10,748 output tokens (31,407 total; 2,033 reasoning-output tokens reported).
This establishes a returned result through the subscription adapter, without
an API-key fallback. It does not establish successful canonical publication.

The draft contains 20 guidance claims, 14 analyst question blocks, seven topics
and six management-tone assessments. The existing validator rejected the first
missing numeric scale. Diagnostic checks found additional period/shape
inconsistencies, five source-unit scale mismatches (million/billion values
paired with scale 1), and an answer-evidence reference outside the topic's
question membership. These are retained draft errors, not accepted facts.
No numeric correction, source relabeling or validator relaxation was applied.

**Zero analyses and zero reviews were published.** Sol review and the remaining
51 original model runs were not started because this extraction failed
validation. The original failed attempt and all native evidence remain intact;
the pilot has two actual model attempts in total. All 251 raw transcript pages
passed hash/preservation checks and the post-activation migration ledger is
unchanged. The exact returned response, diagnostic pointers and completion
audit are in `.local/transcript-codex-retry-20260909/`.

Sixteen focused Codex tests passed, including the bounded per-transport
deadline override and preservation of the default deadline, high reasoning,
failure cleanup and no automatic retry. The source/schema/model configuration
and canonical validators are unchanged. The next extraction revision needs
explicit instructions for numeric scale, shape/period compatibility and topic
citation membership; this one-retry authorization does not admit another
model attempt.


## Extraction request v2 correction — 2026-09-09 UTC

Investigation of the retained ADM draft found an instruction/constraint mismatch:
the provider-facing schema allowed combinations that the existing canonical
validator forbids. Numeric multipliers and topic citation membership also lacked
explicit encoding examples. No source corruption or Codex timeout issue caused
those draft validation failures.

New preparations default to `transcript_analysis_prompt.v2`. Its private
model-facing schema projects the existing value-shape and fiscal-period rules
into nested object alternatives: numeric shapes require positive scale and their
correct point/low/high slots; nonnumeric shapes leave numeric slots null;
calendar/multi-year/other targets cannot emit inferred fiscal labels. All
canonical output fields, the v1 output schema, canonical validators and migration
0011 are unchanged. This is a narrower generation request, not a reinterpretation
or rewrite of previously retained output.

The v2 instructions explain face values versus multipliers, with unscaled,
million and billion examples, single-metric claims, explicit fiscal-label limits
and topic citations restricted to member question/answer turns. Sol's review
instructions explicitly check the same semantic problems. Schema compliance
alone does not establish accurate extraction or correct magnitudes; source-based
validation and pilot review remain necessary.

The request uses supported nested `anyOf` objects under an object root; see
the [official Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).
An isolated native CLI fixture verifies the exact schema is forwarded, high
reasoning and no tools. Independent JSON Schema validation rejects nine
malformed value/target objects in the untouched ADM draft. Topic citation
membership remains enforced by the existing source validator.

New Codex v2 configurations bind the user's **1,200-second per-request limit**.
The existing 3,600-second invocation bound reserves a complete request window
before admitting another call. Terra/high and Sol/high are unchanged. API
execution and legacy requests keep their original limits.

Old prompt v1 remains registered. Execution/publishing selects the exact version
pinned in each immutable plan/configuration. V1 request bytes, configuration
hashes and semantic request identities remain unchanged; a legacy plan cannot
silently upgrade or clear a failed attempt by running under the new default.
A newly prepared v2 plan has a distinct configuration/identity and requires its
own applicable finite execution scope. Rejected responses are never rewritten
or labelled as successfully corrected model output.

Blocked run reports and CLI errors now include bounded validation issue
pointers, making paths such as `/guidance_claims/0/value/scale` actionable while
retaining raw responses before validation. Unknown provider exceptions remain
sanitized. Implementation/verification evidence is retained under
`.local/transcript-analysis-fix-20260909/`.


## Result of the approved v2 live check — 2026-09-09 UTC

The user approved plan
`d564c36b566e02e5374d9d0daec9fcd9b56515cbb9dbb01b7a02d0ddff9376bc`
for ADM fiscal 2026 Q2 only: one Terra/high extraction and one Sol/high review,
at most two new model requests, with a 1,200-second deadline each. Both calls
returned through the existing WSL ChatGPT subscription, with no API-key fallback
or automatic retry. This check's two-call allowance is consumed.

Terra completed in **226.463 seconds**, reporting 22,163 input and 12,363 output
tokens (34,526 total; 3,249 reasoning-output tokens are included in output).
Its unchanged draft contains 19 guidance claims, 14 analyst question blocks,
15 topics and six tone assessments. Guidance value/period field diagnostics
found no errors. This is evidence that the revised constraints worked for those
fields in this sample; it is not proof of complete or accurate guidance.

Four of 88 citations failed exact occurrence checks, all in management-tone
assessments. For example, the model quoted "there are a lot of external
variables." while the cited source says "So a lot of external variables."
The established validator rejected the draft before canonical publication.
No quote was rewritten or validator weakened to admit it.

The approved Sol call reviewed the full source and exact rejected Terra draft
privately, retaining the prescribed model, high reasoning, v2 review request
and 1,200-second limit. It was admitted under the existing job lock and plan
budget, with durable request and response evidence. The private request
identity binds its rejected-draft purpose and input; it has no canonical parent
analysis ID. Canonical review validation/publication was not bypassed.

Sol completed in **332.911 seconds**, reporting 28,133 input and 10,297 output
tokens (38,430 total; 7,760 reasoning-output tokens are included in output).
Its draft declares `needs_changes` with 13 error and three warning findings.
Suggested issues include omitted forward outlook, guidance conditions, composite
metrics, unsupported period/basis labels and analyst-question representation.
Two quotations in Sol's own findings fail exact-source validation, so these are
unvalidated review suggestions, not an accepted review verdict. One suggested
requirement for a joint speaker-by-section tone grid is outside this contract:
the accepted schema requires the separate overall, section and speaker views.
The model review does not establish new requirements.

**Zero analyses and zero reviews were published.** The immutable post-run audit
at `2026-09-09T03:08:43.680884Z` found all six analysis tables empty, preserved
all 251 prior raw transcript page hashes/bytes, verified the source input hash,
confirmed unchanged migration history and found zero analysis-table foreign-key
errors. Both raw response hashes and their native stdout evidence match the
retained request receipts; both native executions exited successfully.
Canonical replay is inapplicable because no output was published.

Private evidence under `.local/transcript-analysis-v2-live-20260909/` includes
`authorization.json`, `preflight.json`, `result.json`,
`draft-review-result.json`, `draft-validation-diagnostic.json`,
`review-validation-diagnostic.json` and aggregate `final-audit.json`.
The ordinary pipeline report retains its one-call blocked outcome; the private
review receipt accounts for the second approved call. Combined reported usage
was 72,956 tokens. No further live request, Equibles fetch, schema change or
recurring-unit action occurred.

This execution-only check changed no production code. The preceding v2 fix
passed 66 focused tests and independent review; those checks were not rerun here.
Citation reliability and extraction/review quality remain unresolved. A
source-selected citation-span design is a possible next correction, not an
implemented feature or a reason to accept the current drafts.


## Evidence summaries and cross-sector checklist — 2026-09-09 UTC

The user explicitly selected relaxed quotation checks, retaining source turn IDs
and substantive numeric/reference validation, and requested a concise guidance
checklist across sectors. This supersedes the prior exact-quotation requirement
for new requests only. Historical failed responses are not rewritten or admitted
under a different interpretation.

New preparations use transcript_analysis_prompt.v3, transcript.analysis.v2 and
transcript.review.v2. Evidence objects contain turn_id and evidence_summary;
paraphrases, case changes and punctuation differences do not block publication.
Empty summaries, unknown IDs, invalid speaker/section/exchange references and
invalid numeric shapes, scales, currencies or period combinations still fail.
A successful validation does not certify semantic entailment or completeness.

The publisher checks the output schema version against its pinned request
configuration before publication. V1/v2 prompt requests preserve exact request
bytes, configuration hashes and semantic identities. Their v1 output validation,
including exact quotes, remains unchanged. The new schema is not a switch for
silently retrying or reinterpreting those old requests.

The existing registry exact_source_spans provenance declaration is satisfied for
summary evidence by a separately labelled exact source-turn span, attached by
code to the retained raw page/hash/JSON pointer. Summary char_start/char_end are
null; source_span always describes the whole referenced turn, not the summary.
The six-table storage, registry, migration 0011, source parser, time/cutoff,
locking and replay contracts are unchanged.

The [cross-sector checklist](TRANSCRIPT_GUIDANCE_CHECKLIST_V1.md) is supplied to
both Terra and Sol. It covers revenue/demand, profit/margins, costs, cash/capital,
operating execution, sector KPIs, assumptions and guidance changes/refusals.
Its reconciliation rules connect prepared remarks and Q&A, preserve qualifiers
and distinguish explicit targets from historical facts and generic aspirations.
This implements a prompt checklist, not a machine-enforced completeness ledger.
No company is required to supply every listed metric.

Use the [analysis schema](TRANSCRIPT_ANALYSIS_V2.schema.json),
[synthetic filled example](TRANSCRIPT_ANALYSIS_V2.example.json) and
[review schema](TRANSCRIPT_REVIEW_V2.schema.json). Numeric shapes and target
constraints still receive the v2 generation restrictions. Unknowns stay null;
qualitative statements and refusals do not become fabricated numbers.

Terra/high extraction and Sol/high pilot review, 1,200-second Codex deadlines,
existing request caps and no automatic retry are unchanged. This local change
does not admit a live pilot, Equibles fetch or recurring-unit operation.

Validation passed 78 unique focused tests, followed by 12 overlapping affected
rechecks after the source-span addition. A fresh independent verifier passed the
integrated change, independently ran eight overlapping memory-only tests, and
checked schemas/examples and legacy fingerprints. Focused private-version
checks apply; no shared safety or migration change required the full suite.
No live extraction quality claim follows from these offline checks.
Private receipts are under .local/transcript-evidence-summary-20260909/.

### Structured coverage implementation and rerun — 2026-09-09 UTC

The user requested implementation of the four proposed coverage changes and
another run. Prompt v4 / analysis and review v3 add the coverage ledger, repeated
mention reconciliation, topic-to-candidate links and precise finding types.
The six-table storage contract is unchanged: coverage lives in the retained
analysis output and derived coverage JSON; links and findings remain in their
existing item/review JSON. No migration is required. Prompt v1/v2/v3 request,
configuration and identity vectors remain frozen.

Authorized workload: ADM fiscal 2026 Q2, capture
equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91; one Terra/high extraction and
one Sol/high pilot review through the existing Codex subscription, at most two
new model requests, each capped at 1,200 seconds, without automatic retries or
API fallback. Preparation and outcomes are retained under
.local/transcript-coverage-v4-20260909/. This decision does not expand the older
whole-ticker pilot or trigger Equibles collection.

### Result of the approved structured-coverage pilot — 2026-09-09 UTC

The finite ADM fiscal 2026 Q2 rerun completed both approved subscription calls:
Terra/high in 278.870 seconds and Sol/high in 431.374 seconds. Terra returned
24 draft guidance claims and 19 candidates, but candidate gc5 included t23
without retaining it in linked claim c5 evidence. The publisher rejected the
draft before any canonical write. Sol reviewed that exact rejected draft as the
already-approved second request, returning needs_changes with 18 error findings
and one warning. Its private review validation passed; it was not assigned an
invented canonical parent or published.

The new evidence-summary checks passed, and some prior omissions were recovered,
but missing qualifications, semantic mistakes and invalid Q&A links remain.
Both raw outputs are retained in .local/transcript-coverage-v4-20260909/ with
the detailed pilot report and aggregate audit. Exactly two model calls ran,
with no retry or API fallback. All 251 pre-existing transcript pages and the
migration ledger were preserved; all six analysis tables remain empty.
Eighty-nine focused tests and fresh independent verification passed before
execution. The workload is complete; additional calls require finite scope.

### Private Sol extractor comparison — 2026-09-09 UTC

The user accepted a controlled Sol/high extraction comparison on the same ADM
fiscal 2026 Q2 transcript. This is a one-request private pilot, not a fleet-wide
default change. The extraction prompt v4, analysis schema v3, high effort,
input/output bounds and 1,200-second Codex deadline are unchanged; the serialized
model request differs only in its model field. Prior v1/v2/v3/v4 Terra extraction
and Sol review request/configuration/identity vectors remain unchanged.

The current company migration 0011 permits only Terra in the published-analysis
model column. It is not edited or bypassed. The dedicated
quant_data.operations.transcript_analysis_pilot entry point prepares and executes
one Sol extraction through the existing subscription adapter, immutable source
reader and job-lock primitive. It retains the exact input, raw response, attempt
receipt and validation report under
data/.operations/transcript-analysis/extractor-pilots/. It never invokes the
canonical publisher and cannot run a review or select an API billing backend.
A failed or uncertain attempt cannot be retried through a newly prepared plan;
a received response replays privately without credentials or model calls.

Example for this explicitly authorized scope:

    PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis_pilot prepare --capture-id equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91
    PYTHONDONTWRITEBYTECODE=1 python3 -m quant_data.operations.transcript_analysis_pilot execute --plan-id <prepared-plan-id>

The model helper's optional extractor_model selector supports Terra or Sol
extraction on the current prompt; reviewers and legacy prompts retain their
original routing. Selecting Sol is not permission to publish it. Any eventual
production default/storage change follows a separate decision after assessing
the pilot's accuracy and resource usage.

#### Result of the private Sol extractor comparison — 2026-09-09 UTC

The one Sol/high extraction completed in 640.035 seconds, returning 31 guidance
claims and 28 candidates. Reported usage was 23,309 input and 19,884 output
tokens (43,193 total; 3,919 reasoning tokens). The current 16,384 output-token
bound rejected the retained result. A separate offline diagnostic found two
wrong topic/candidate links; it did not waive that resource failure.

Against eight source-adjudicated Terra defects fixed before the call, Sol
corrected six, partly corrected one and repeated the EPS/operating-profit
conflation. It also recovered technology-savings guidance and improved source
ambiguity/exclusion handling. This one-case comparison is not a fleet accuracy
estimate. Details and further limitations are in
.local/transcript-sol-extractor-20260909/comparison-report.md.

No canonical rows or migrations changed. All 251 prior transcript pages were
preserved; private replay made zero model calls. The original one-call allowance
is exhausted, with no retry, reviewer call or API fallback.


## Readiness correction - September 12, 2026 UTC

The user requested a rapid fix of the demonstrated transcript-analysis failures.
New preparations use prompt v5; the analysis/review schemas and six-table storage
remain v3 and migration 0011 is unchanged. Terra/high extraction and Sol/high
review remain the defaults; Sol extraction remains a private comparison option.

V5 raises the explicit output allowance from 16,384 to 32,768 tokens, including
reasoning. Configuration, serialized requests, preparation reservations, immutable
plan checks and execution checks use the pinned version's allowance. V1-v4
requests/configurations/identities and their 16,384-token limits remain unchanged,
including retained Sol v4 pilots. No old failed output is admitted under v5.
At the existing pinned API rates, a new extraction reserves USD 0.793216 and a
new review USD 1.455360 (USD 2.248576 paired); these are conservative reservations,
not a current pricing or billing guarantee. Subscription runs use the shared
subscription allowance, with no API fallback; their output limit remains a
post-completion check. Input/response byte bounds and deadlines are unchanged.

A code-generated reference_index carries management turn IDs, sections and the
authoritative question/answer inventory. It supplements the complete original
source without changing its text, stored legacy inventory or input hash.
Configuration pins courtesy_only.v2 for new requests; v1-v4 continue using v1.
V2 conservatively recognizes complete greeting, congratulations and acknowledgement
phrases seen in the retained ADM source. Any additional substantive wording
keeps the contribution eligible. Excluded courtesies still end the preceding
answer exchange. The publisher, private pilot validator and returned inventory
use the policy pinned to the request. Original source evidence remains intact.

V5 instructs the model to use descriptive IDs, reconcile candidate source turns
with claim evidence, and match Q&A links by both source exchange and meaning.
It explicitly separates EPS conditions from operating-profit cadence, retains
range flexibility, timing and other qualifications, preserves uncertain scope
and avoids double-counting overlapping savings. Existing reference, evidence,
numeric and coverage checks remain enforced; no automatic link rewriting,
semantic repair, additional model request or retry is introduced.

Offline validation and the saved ADM-source diagnostic are retained under
.local/transcript-readiness-fix-20260912/. This change prepares the next bounded
pilot; prompt instructions and offline tests do not establish live extraction
accuracy or fleet readiness. No model/provider calls or canonical writes are
part of this implementation. A fresh extraction/review workload must be finite;
the exhausted September 9 pilots are not automatically resumed.

The final saved-ADM diagnostic preserves the source hash and all original output
bytes. It finds 11 substantive question blocks instead of 14, excluding t12,
t14, t20 and t30; t12 was already excluded by v1. Both rejected historical drafts
still fail the original validation rules. This demonstrates the inventory fix
and preserved rejection behavior, not corrected model-generated semantics.

Validation completed with 113 distinct focused tests passing: the integrated
112-test run passed in 100.510 seconds; the new paired courtesy extraction/review
regression brought the readiness suite to ten tests (6.534 seconds). After the
final parent-policy propagation correction, 37 affected contract, coverage and
evidence tests passed again in 14.845 seconds. Review validation uses the parent
analysis's pinned courtesy policy, preserving older parent behavior. These
rechecks overlap earlier cases and are not counted as additional coverage.

The first focused run's eleven compatibility failures and the subsequently found
review-policy failure are retained as evidence. Corrected checks and the new
paired regression pass; no validation rule or old response was bypassed.
The full repository suite was not run: this change is confined to the private
transcript request/validation workflow, with no shared schema, migration, lock,
identity, time or canonical storage changes.


### September 12, 2026 UTC - approved ADM v5 transcript pilot

The user accepted the proposed one-capture ADM fiscal 2026 Q2 pilot with
"OK pls go ahead": at most one Terra/high extraction and one Sol/high review
through the existing Codex subscription, each capped at 1,200 seconds and
32,768 output tokens under prompt v5. Review follows only a validated,
published extraction. There is no automatic retry or API fallback.
The scope is capture equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91,
using the fixed company store and existing immutable reader/publisher/locks.
No Equibles request, migration, recurring-unit change or bulk extraction is
included. The 113-test offline correction and independent review passed.
Preparation and actual results are retained under
.local/transcript-adm-v5-live-20260912/; approval does not assert completion.


#### Result of the September 12 ADM v5 pilot

Terra/high completed one subscription call in about 4 minutes 54 seconds,
reporting 24,247 input and 16,133 output tokens (40,380 total). Its retained
draft contains 22 guidance claims, 22 candidates, 11 substantive question
blocks and 12 topics. The v2 courtesy inventory works on the live output.
The publisher rejected candidate biofuels_second_half because its linked EPS
claim omits source t19. Offline reference inspection also found an unrelated
phase-one investment candidate linked to the EPS-scenarios topic.

Source adjudication found remaining EPS/operating-profit conflation, omitted
Q4 flavors seasonality, unsupported flavors-only operating-profit scope, and
omitted global-technology savings. Prompt v5 does not yet establish extraction
accuracy or readiness for bulk processing.

No analyses or reviews were published. The pilot source bytes/hashes and all
17,855 preflight raw-page metadata records were preserved; ongoing independent
fetching added pages. The company migration ledger is unchanged and the six
analysis tables have no foreign-key errors. No model retry or API fallback ran.

An attempted change to use the second Sol review privately on the failed draft
was rejected by automatic approval review before execution: the approved review
was conditional on a validated extraction. Exactly one model request was used;
no second call, private-review process or workaround started. Reviewing this
rejected draft requires renewed explicit approval. Evidence is retained under
.local/transcript-adm-v5-live-20260912/, including final-audit.json,
reference-diagnostic.json, quality-check.json, review-approval-block.json and
completion-receipt.json. Production implementation was unchanged during the run.

## September 12, 2026: Sol/xhigh and Astra/xhigh private pair

The user accepted Sol/xhigh extraction with Astra/xhigh review and requested
implementation plus one live test. The fixed profile is
transcript_analysis_pair_prompt.v1, based on the existing v5 prompt and
v3 analysis/review schemas. quant_data.company.transcript_analysis_pair
owns its exact models, efforts, instructions, configurations and request bytes;
the canonical Terra/high + Sol/high defaults and earlier identities remain unchanged.

quant_data.operations.transcript_analysis_pair_pilot prepares and executes
one exact stored capture, with at most two subscription calls (one per model),
1200 seconds per call, a 32768-token post-completion output validation limit,
no automatic retries, and no API fallback. Its private state root is
data/.operations/transcript-analysis/model-pair-pilots. The existing pinned
native Codex CLI and ChatGPT login provide authentication.

Astra reviews a schema-shaped Sol draft even when semantic validation fails.
Malformed/model-mismatched responses and hard input/response byte failures cannot start review. A schema-shaped draft that exceeds the subscription output-token limit remains rejected, but may receive its one already-planned private review if the unchanged review input fits. Either stage exceeding that output limit prevents the pair from passing.
The review validator checks the original draft's pointers, candidate/claim
links, source evidence and verdict consistency without repairing its parent.
The pair passes only when extraction validation and review validation pass
and Astra accepts. All outputs remain private. Canonical review still
requires a fully validated parent; the private review adds no bypass.

Each attempt is durably reserved before the call. Received responses are
hashed and replayed with no provider request or credential access. A failed
or uncertain attempt cannot be retried through another plan. The review
identity also binds the exact raw extraction response hash.

Approved live scope: ADM fiscal 2026 Q2, capture
equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91, two calls total,
including review of a semantically rejected draft. Evidence is retained at
.local/transcript-model-upgrade-20260912/. This is a new paired test, not a
retry of the previously blocked Sol review of Terra's draft.

Canonical activation needs an additive migration because applied company
migration 0011 restricts stored model/effort pairs. No applied migration,
canonical table, recurring unit, or provider-fetch workload is changed here.

The upgraded private-pair implementation passed 76 focused offline tests (66 compatibility/contract checks and 10 new paired-run checks). The live launch was rejected by automatic approval review before execution: explicit trusted user approval is required to send the saved ADM transcript to OpenAI. No upgraded model call or canonical publication occurred; all launch/native-attempt markers were absent. Evidence: .local/transcript-model-upgrade-20260912/live-approval-block.json and completion-receipt.json. The prepared plan remains unexecuted.

The user subsequently replied “yea go ahead” to the explicit ADM transcript
transfer question, authorizing the named OpenAI destination and both calls,
including Astra review of a rejected draft. The first local runner stopped
before model calls because the shared registry advanced to pending Sharadar
migration 0017 while the store retained its verified 0016 prefix.

The private pair now uses ready_for_private_evaluation: every installed
company migration row must equal the corresponding current-registry prefix,
transcript migration 0011 must be present, and table/dataset/identity checks
must pass. The canonical full-ledger guard remains strict. No migration is
applied or changed. Twelve paired-run tests, including an actual predecessor
temporary store and corrupt/missing-prefix cases, passed; a read-only review
found no blocker.

The same approved plan resumed with zero prior model calls. Live evidence is
.local/transcript-model-upgrade-live-20260912/; the original preparation,
approval rejection, and zero-call readiness-stop evidence remain preserved
in .local/transcript-model-upgrade-20260912/.

The resumed Sol call returned a schema-valid, reference-valid draft with 43
claims, 51 coverage candidates, 11 substantive question blocks and 11 topics.
Its 36211 reported output tokens (including 14003 reasoning tokens) exceeded
the unchanged 32768 post-completion validation limit. The output remains
rejected on that limit. Its unchanged Astra review request is 193069 bytes,
within the existing 200000-byte input bound.

The private runner retains this limit failure while permitting the already
approved review of the failed draft; it cannot turn the pair into a passing
result. Hard input/response bounds, model checks, canonical validation and
all request limits remain unchanged. Fourteen paired-run tests passed after
this correction, including over-limit extraction/review rejection and zero-call
replay. The retained Sol response is replayed locally; only the second
approved Astra call is used. No Sol retry or extra call is authorized.

The approved pair is now complete: exactly two model calls were used.
Sol returned 43 claims and all 11 substantive question blocks, passing
structural/reference checks but exceeding the unchanged output-token limit
(36211 versus 32,768, including 14,003 reasoning tokens). Astra completed with a
needs_changes verdict and four findings. Its review was recovered offline
from preserved native events after a CLI 0.144.4 missing-Astra-metadata warning.
The exact warning is now explicitly retained by the transport rather than
classified as a tool; other errors/tools still fail. Effective Astra xhigh
reasoning was not verified. Three review pointers include an invalid wrapper,
so strict review validation also fails.

No canonical publication or production activation occurred. Both model calls
are spent; no further call or retry was made. The immutable final audit
confirmed all 18,780 prior raw pages, exact source, migration ledger and
analysis rows unchanged, with zero foreign-key errors. Eighty-two distinct
focused checks passed across the implementation; final 32 affected checks
passed after warning handling. Results and unresolved work are retained in
.local/transcript-model-upgrade-live-20260912/LIVE_TEST_RESULT.md and
completion-receipt.json. Original blocked-attempt receipts remain unchanged.

Structured implementation validation: 86 focused offline tests passed in 35.424 seconds. The requested ADM launch was rejected twice by automatic approval review before execution, including after prior explicit transfer approval was retrieved. No new model call started; fresh explicit confirmation of this repeated full-transcript transfer remains pending. Private completion evidence is in .local/transcript-structured-live-20260912/completion-receipt.json.


The user subsequently explicitly approved the full-transcript transfer. The two
structured ADM calls completed on 2026-09-12 at 16:46:59 UTC: Sol/xhigh extraction
and Astra/xhigh review, with no retries or API fallback. Sol returned a 582-word
record; Astra revised it to 521 words. Both pass schema, numeric, source-reference
and length checks and stay below the completion-token bound. Native Astra
fallback metadata still prevents verification of its effective reasoning effort;
the automated pair therefore retains needs_changes status.

A subsequent primary-assistant source check restored the capex-above-range caveat,
preserved conflicting Q4 coverage wording, and made the Q4 flavors seasonal low
explicit. These three corrections are separate derivative artifacts, not changes
to either original model response. The checked display is 544 words.
This example does not establish unattended extraction accuracy.

The immutable post-run audit verified the source, source-page metadata, migration
ledger and canonical analysis rows unchanged, with no foreign-key errors. Cached
replay used zero credentials and zero model calls. The finite two-call allowance
is consumed. See .local/transcript-structured-live-20260912/completion-receipt.json
and final-audit.json; the source-corrections.json file records the display edits.


## Explicit Terra/high and Sol/medium ADM comparison - 2026-09-12

The user requested: "OK now can we dial down the model, ie use terra high for
extraction, and sol medium to validate for this example?" This supplies one
new finite two-call comparison for the same saved ADM source-labelled 2026 Q2
capture equibles_transcript_94c0ceecdd9e040ee16ba5fb8f73df91. Prior explicit
approval of the full transcript and resulting draft transfer to OpenAI through
the existing Codex ChatGPT subscription continues to cover that destination.

At most one gpt-5.6-terra/high extraction and one gpt-5.6-sol/medium final-editor
review are allowed, with the same complete source, prompts, structured schemas,
word budget, 1,200-second per-call deadline and 32,768-token completion bound.
The review may use the one allotted call to correct a structurally readable
draft that fails semantic checks. No hidden retry, API fallback, new Equibles
fetch, canonical publication, migration or recurring-unit change is included.

The explicit private selector is structured_terra_sol, with configuration
transcript_structured_call_terra_sol_prompt.v1. The existing structured default
remains Sol/xhigh then Astra/xhigh; frozen historical configurations remain
unchanged. The transport binds the comparison to its fixed model, effort, role,
prompt and schema and retains codex_exec.v2 evidence for both stages.

Private code snapshots, offline checks, source, plan, authorization, native
receipts, model output and immutable audit evidence belong under
.local/transcript-terra-sol-live-20260912/. This dated request entry records
scope and is not evidence that the new model calls have completed.


The Terra/high and Sol/medium comparison completed at 2026-09-12T17:24:39Z,
using exactly two calls and no retries. Native model time totaled 102.013
seconds (Terra 44.555; Sol 57.458). The 520-word draft became a 581-word final
record. All automated checks passed, with no native metadata warning recorded.

The primary-assistant source check nevertheless found missing capex flexibility,
an unflagged Q4 coverage conflict, omitted flavors seasonality/incomplete Decatur
recovery, and omitted explicit half-year/quarter operating-profit cadence.
The retained unedited model result remains automated validated_private, while
the separate source-quality assessment is needs_correction. No default profile
promotion is implied. Exactly this finite two-call allowance is consumed.

Ninety-two focused offline tests passed in 40.443 seconds. The immutable audit
confirmed the scoped source, source-page evidence, migration ledger and canonical
analysis rows unchanged, with zero foreign-key errors. Replay used no credentials
and zero model calls. No canonical write, provider refetch, migration or recurring
unit action occurred. Evidence:
.local/transcript-terra-sol-live-20260912/completion-receipt.json,
COMPARISON_RESULT.md, source-quality-check.json and final-audit.json.


## Ten-ticker saved-history extraction with sampled review - 2026-09-12

The user requested full-history Terra extraction for ten tickers using the
current approach, followed by a few random Sol quality checks and an aggregate
report. The user selected the first ten eligible saved tickers: A, AA, AAL,
AAMI, AAOI, AAON, AAP, AAPG, AAPL and AAT. Read-only inventory found 234
distinct saved source-labelled quarters across those tickers. Full history here
means all saved quarters at preparation, retaining source fiscal labels and
choosing the latest capture if a quarter has multiple captures.

This current decision authorizes at most one Terra/high extraction per saved
quarter (234 maximum) and five Sol/medium reviews sampled uniformly without
replacement from structurally readable drafts. Full saved source text, and the
resulting draft for reviews, go to OpenAI through the established Codex ChatGPT
subscription. The schema, prompts, 1,200-second call deadline and 32,768-token
post-completion output check are unchanged. Three concurrent model requests
are permitted within this finite workload. No retry, API fallback, Equibles
refetch, canonical publication, migration or provider scheduling action is added.

The batch uses transcript_structured_call_terra_sol_prompt.v1 and the existing
private replay-safe exchange receipts and native attempt markers. It freezes
source bytes, exact request hashes, the random seed, sampled capture ids and
the complete finite manifest. Item failures are retained and other quarters
continue; three consecutive transport failures stop new queued requests.
The batch cannot silently replace failed reviews or re-extract completed items.
Sample approvals and edits are aggregate review outcomes, not a measured
population accuracy percentage. Unreadable/failed extractions remain separately
counted and do not disappear from the completion denominator.

Implementation: quant_data/operations/transcript_history_batch.py.
The durable state root is data/.operations/transcript-analysis/history-batches;
existing model-pair-pilots and codex roots retain native request evidence.
Private launch/test/audit evidence: .local/transcript-history-10-20260912/.
This records the requested finite scope, not completion of live model work.


Ten-ticker batch readiness: 62 focused offline tests passed in 32.388 seconds.
The complete 234-source preflight passed, with no rejected input. Frozen plan:
87869db0d215f1df0fef2318660ec1e09219b01149bccf7f8abd3b856a4008d1.
Automatic approval review rejected the live launch before execution because
the trusted user messages did not explicitly approve exporting this batch's
full transcript payload and five sampled drafts to OpenAI. No workaround or
indirect launch was attempted. Launcher, start, model-attempt and result
markers and the batch run directory are absent; new model calls remain zero.
The exact prepared finite batch is retained awaiting that transfer approval.
Private evidence: .local/transcript-history-10-20260912/launch-block.json and
completion-receipt.json. No completion monitor was created for an unstarted run.


The user subsequently explicitly replied "yes approved" to the exact full
234-transcript OpenAI transfer and five sampled transcript/draft review question.
The prepared batch launched at 2026-09-12T22:57:48Z as Linux PID 541576, with
native model-attempt evidence confirmed. The rejected-launch completion receipt
is preserved separately as launch-block-completion-receipt.json. The same
239-call maximum, three concurrent calls, timeout and no-retry rules apply.
This is running-state evidence, not a completed quality result.

A proposed recurring completion heartbeat was rejected by automatic approval
review because that persistent automation was not explicitly requested. No
heartbeat was created and no workaround automation was installed; the active
task follows the running finite batch directly.


The ten-ticker batch completed at 2026-09-12T23:52:57Z, approximately 55 minutes
after launch. All 234 saved quarters produced structured Terra/high output;
83 passed every automated check and 151 were flagged, predominantly for
source-reference and repeated-content rules. Five randomly sampled Sol/medium
reviews completed: zero unchanged approvals, four revised records passing
the review checks, and one record still needing attention. These are validation
and sampled review outcomes, not a population factual accuracy measurement.

Exactly 239 model calls were consumed, with no retry or additional call during
the reporting follow-up. All 239 retained response hashes and the frozen random
selection were verified locally. The immutable completion audit confirmed source
text, selected pages, migration ledger and canonical analysis rows unchanged.
No canonical publication occurred. The workload is complete; no completion
heartbeat is required or was created. Aggregate evidence is retained in
.local/transcript-history-10-20260912/aggregate-quality.json and
AGGREGATE_QUALITY.md, alongside the original completion receipt and audit.


## Tiered structured-call quality standard and fresh five-review sample - 2026-09-13 UTC

The user accepted the annotated standard: substantive errors block acceptance;
missing speaker labels or conflicting source statements are uncertainty flags;
repetition and modest length overruns are editorial warnings. The user requested
implementation and another random sample of five reviews of the retained
structured extractions. This supplies exactly five new Sol/medium calls.
The already explicitly approved full saved transcript and draft transfer to
OpenAI through the Codex ChatGPT subscription is reused for the same retained
population. No Terra extraction, provider refetch, retry, canonical publication,
migration or recurring automation is part of this operation.

The new private profile is transcript_structured_tiered_prompt.v2, selected as
structured_tiered by the paired runner. It keeps the existing structured brief
schema and introduces transcript.structured_quality_review.v2 with classified
draft findings, separate source uncertainties, and unresolved substantive issues.
Legacy profiles, source role mapping, canonical schemas and historical evidence
remain unchanged. The review-only runner uses the existing private exchange
receipts, binds each review to its original Terra response hash, and excludes
the five previously sampled records before freezing a fresh random sample.

The local assessment collects findings rather than stopping at the first one.
Incorrect identity, impossible/contradictory numerical values, incompatible
guidance, nonexistent references and known analyst/operator statements attributed
to management remain blocking. Unknown speaker roles remain unknown and flag
attribution uncertainty; they are never silently assigned to management.
Repeated comparison labels across distinct metrics are valid. Exact duplicate
metrics with identical values are editorial; conflicting same-metric values
block. Modest cell/document overruns up to 25% are editorial, while greater
overruns and existing schema/request bounds still block. Entire summaries cannot
exceed the source word count. Source-context factual checking remains the model
reviewer's responsibility; mechanical acceptance is not a factual accuracy score.

One Sol review compares each original saved Terra draft with its complete saved
source and the local assessment. Draft errors remain classified as substantive
even when the reviewer corrects them. Source uncertainties do not themselves
force needs_attention; only unresolved substantive/hard-bound problems do.
No historical extraction, review or summary is rewritten by reassessment.

Seventy-seven focused offline checks passed in 35.028 seconds, including 15
tiered-policy/resampling checks and prior profile/transport/history compatibility.
The private frozen plan, reassessment, exact request hashes, native attempt and
completion evidence belong in .local/transcript-tiered-review-20260913/.
This entry records the implementation and finite authorization, not completion
of the five live review calls.


Tiered implementation follow-up: the final 78 focused checks passed in 37.000
seconds. The local reassessment retained 38 records without flags, 165 with
uncertainty/editorial flags, and 31 with substantive or hard-bound blockers
(203/234 mechanically unblocked). This is not a factual accuracy result.
A malformed retained point measure exposed a renderer exception during the
first local preflight. The checker now keeps its numeric blocker and marks
word count unavailable without stopping the population assessment; the original
failure and the added regression check are retained. No model call was used.

The five fresh Sol/medium reviews are frozen as plan
f5736d90f90855d961fc6025835f39f84b4466bd6f6aab000ed5c28e96213ab7,
excluding all five previously sampled records. Automatic approval review
rejected the launch before execution and requires explicit approval for this
additional five-source/five-draft transfer to OpenAI. No workaround or retry
was attempted. Start, attempts, result and audit markers and all new review
reports are absent; new calls remain zero. Evidence:
.local/transcript-tiered-review-20260913/launch-block.json,
completion-receipt.json and IMPLEMENTATION_RESULT.md.


### Tiered five-review completion, 2026-09-13 01:46 UTC

The user explicitly approved the exact additional five full saved sources and
original Terra drafts being sent to OpenAI for Sol/medium review with
"sure go ahead". The transfer approval is retained at
.local/transcript-tiered-review-20260913/explicit-transfer-approval.json.
This supersedes the pending-launch state above for the same frozen plan
f5736d90f90855d961fc6025835f39f84b4466bd6f6aab000ed5c28e96213ab7.
The previous blocked completion receipt was preserved separately as
launch-block-completion-receipt.json; its historical rejection was not erased.

Exactly five review calls completed from 01:40:29 through 01:46:29 UTC.
Three review packages passed; two failed the category/severity consistency
check in reviewer findings. Both failed raw reviews remain intact and are not
promoted to passed status. Separate local checks accepted all five revised
briefs with nonblocking flags. Sol reported substantive draft findings in all
five raw reviews, missing substantive Q&A in four, and source uncertainty in
four. The original live summary counts substantive findings and uncertainty
only where review validation completed; aggregate-quality.json explicitly
distinguishes that subset from raw claims in the two unvalidated packages.
This sample is not a population accuracy estimate.

The final audit confirms all 234 original source/draft pairs unchanged,
zero new extraction calls, zero model calls or credential access on replay,
and no canonical database access or publication. There were no retries.
The finite five-call approval is consumed; it grants no further run, repeat,
retry or scheduled operation. Completion and readable aggregate evidence:
.local/transcript-tiered-review-20260913/completion-receipt.json,
final-audit.json, aggregate-quality.json and REVIEW_RESULT.md.
The tested implementation remains the 78-check baseline recorded above.


### Astra/medium adjudication of the five Sol reviews - 2026-09-13 UTC

The user explicitly requested Astra/medium to assess Sol's review conclusions
against the raw transcripts, the extracted structured information and the goal
of concise, informative structured call summaries, and to issue a final verdict.
The bounded scope is one fresh Astra/medium adjudicator covering the same five
cases from tiered resample plan
f5736d90f90855d961fc6025835f39f84b4466bd6f6aab000ed5c28e96213ab7,
including both Sol packages whose review labels failed validation.

The route is a fresh default-role Codex subagent with explicit gpt-6-astra and
medium overrides, as requested by the user, rather than the preset verifier's
xhigh effort. Its inputs are hash-verified private copies of the five complete
saved source-turn sets, original Terra drafts, and unedited Sol reviews with
revised briefs. The original 234 source/draft pair hashes were also verified.
This uses one bounded delegated adjudication session; it does not create five
new CLI review calls, refetch transcripts, re-extract drafts, change the
production workflow or access/publish to the canonical database.

Only new private verdict artifacts may be written by the adjudicator. No
recursive delegation or automatic repeat is included. Parent-owned evidence:
.local/transcript-astra-adjudication-20260913/authorization.json,
input-manifest.json and started.json. This entry records authorized dispatch,
not completed adjudication.


Astra/medium adjudication completed for all five bundles and all 235 source
turns. The fresh adjudicator classified Sol's 15 findings as 1 substantively
justified, 13 valid but nonblocking, and 1 unsupported/overstated. Only 1 of
Sol's 10 labeled blockers warrants blocking, on a narrower core-theme rationale.
Astra judged the original Terra briefs: 1 usable as is, 2 usable with caveats,
2 needing targeted corrections. Sol revisions: 1 usable as is, 3 with caveats,
1 needing a targeted correction. These are independent sample judgments, not
population accuracy or an alteration of the prior saved validation statuses.

Astra found that empty analyst-focus fields often coexist with Q&A substance
already included elsewhere. It also identified a risk-wording issue Sol did
not list, one material qualification omitted by a Sol revision, and three
malformed revised topic titles. Its verdict favors useful existing drafts and
a compact correction list; it does not justify full re-extraction or a workflow
rewrite. No recommended correction or production policy change was applied
within this adjudication-only request.

Parent checks reconciled every finding index and aggregate count, confirmed all
cited source-turn IDs exist, and verified the 15 protected original files and
all five frozen bundles unchanged. The two verdict files have mode 600.
One requested Astra/medium delegated session was used; no extractions, Codex
CLI calls, provider fetches or canonical database operations were performed.
Evidence: .local/transcript-astra-adjudication-20260913/ASTRA_VERDICT.md,
astra-verdict.json, final-audit.json and completion-receipt.json.


## Structured transcript database storage - authorized September 13, 2026 UTC

The user requested that the saved structured extractions be stored in the
database with source links, model/version information and review status.
This authorizes the finite import of the existing 234 original Terra/high
outputs, their 234 retained automatic assessments, 10 Sol/medium reviews and
5 Astra/medium per-case adjudications into data/company.sqlite. No model call,
provider request, re-extraction, source rewrite, recurring-unit action or public
exposure is included. Storing a draft does not mark it factually accepted.

Allocate company ordinal 18, company:0018_structured_transcripts, after the
verified company 0017 ledger. The new private derived dataset
company.transcript.structured owns company_structured_transcript_outputs and
company_structured_transcript_assessments. It has no collector, tool, dashboard
or export binding. Original structured output and raw model evidence are
immutable. Separate assessments retain mechanical status, failed Sol package
status, original Sol revisions, and Astra disagreement without overwriting the
original Terra draft. Model availability timestamps remain distinct from the
database publication timestamp; as-of reads exclude later assessments.

The active registry stays at 2.82 during development. The candidate registry
2.83, forward migration checksum and exact predecessor proof are pinned in
.local/transcript-db-publication-20260913/allocation.json. The prepared import
is a bounded hash-addressed manifest with per-record files and no database or
network access during loading. All 234 retained source/draft identities and
all reviewer request/response bindings were checked. Publication must pass the
full offline suite on the isolated candidate baseline and fresh independent
verification before activating the registry and applying the migration.
Source lineage, immutable rows, rollback, cutoff and exact zero-write replay
are covered by ten passing focused temporary-store checks. Allocation and
private artifacts are not proof of canonical application; completion evidence
will be appended after the authorized operation.


### Structured transcript storage completion - September 13, 2026 UTC

The authorized existing-batch import completed at 2026-09-13T05:03:17.263570Z: 234 original
Terra/high structured outputs and 249 separate assessments (234 automatic,
10 Sol reviews, 5 Astra adjudications) are stored in data/company.sqlite.
Storing drafts preserves their original quality status; it does not assert
acceptance or replace them with reviewer revisions. No extraction/review pipeline
model calls, provider requests, recurring-unit changes, source rewrites or public
exposure were performed.

Company migration company:0018_structured_transcripts was applied with
SHA-256 22d587532a4f5ecffbc76e2e16b793b312fc9f116815474328ec4e2ba05d9111.
Actual migration registry provenance: 2.83.0.
All prior company migration rows and existing SQL definitions were preserved.
All 234 canonical source records and original extraction bytes, plus all 249
assessment payloads and evidence bytes, matched the prepared batch after import.
The new tables passed foreign-key checks. Exact replay wrote zero records,
created no new ingestion run, and left the dataset's stored state unchanged.

The complete reconciled offline gate covered 2381 unique cases
with 0 skips and zero unresolved failures/errors. Required
correction cases passed actual reruns. Fresh independent review cleared the
implementation, frozen-runtime harness and evidence reconciliation methods.
No full-suite certification of unrelated shared-runtime changes is claimed.

During validation, unrelated work advanced the shared active registry beyond
the initial 2.82 development baseline. The company declarations remained exactly
compatible. This import used the hash-verified frozen 2.83 runtime and preserved
the active registry, which was 2.84.0 at final verification.
This supersedes only the earlier prospective registry-activation sequence;
the authorized population, storage semantics and gates did not broaden.

Evidence: .local/transcript-db-publication-20260913/completion-receipt.json,
canonical-schema-audit.json, canonical-final-audit.json, full-suite-result.json,
frozen-runtime-verification.json and DB_STORAGE_RESULT.md. Future extraction
auto-publication was not enabled by this existing-batch import.


## Full saved-universe Terra extraction and incremental storage - September 13, 2026 UTC

The user requested: "OK great, now pls proceed with the extractor for the full
universe, and store the extracted outputs in our db". This supersedes the
prior ten-ticker extraction boundary for unprocessed saved quarters. The
completed 234-quarter population remains excluded from repeat extraction.

The frozen inventory at 2026-09-13T14:45:28.377236Z contains 28,030 saved
source-labelled quarters across 1,231 tickers. Of these, 234 are already in the
structured dataset. The finite requested remainder is 27,796 quarters across
1,221 tickers, selecting the latest saved capture per source-labelled quarter.
Source-preflight failures remain in the denominator and consume no model call.
Newly fetched transcripts after this snapshot do not extend this batch.

The established Terra/high structured profile and Codex ChatGPT subscription
transport are reused: at most one request per ready quarter, up to 27,796
extraction calls, three concurrent requests, 1,200-second deadline, zero
automatic retries, zero new Sol/Astra transcript-review calls, zero Equibles
requests and no API billing fallback. Full saved transcript text goes to OpenAI
through that existing extraction transport. No recurring unit is added or
modified; this is a finite checkpointed process.

Implementation: quant_data/operations/transcript_universe_batch.py. Each
schema-shaped original output and its tiered automatic assessment are published
through StructuredTranscriptPublisher into company.transcript.structured.
Quality flags and blocked drafts remain explicit. Malformed responses are
retained privately and other quarters continue. Three consecutive transport
failures pause queued work; source-integrity or publication failures also pause
new requests. Already attempted failed model requests are not retried on resume.
Unattempted entries remain within the same fixed cap. Publication recovery
reuses verified saved response bytes, creates no new model request, and uses
the existing zero-change replay semantics after a prior commit.

The runner stores hashed plan pages and full source snapshots under
data/.operations/transcript-analysis/universe-batches, reusing the existing
model-pair-pilots and codex evidence roots. It takes short existing company
locks for immutable source reads and publication, with network work outside
database locks. No schema, migration, shared registry, scheduler, store path,
credential mechanism, or public tool/export changes are included.

Thirty-one distinct focused checks passed: eleven new temporary-store runner
checks plus ten existing history-batch and ten existing structured-storage
checks. The initial new-test fixture helper collision was corrected and all
eleven new tests reran successfully. Tests used synthetic responses and explicit
temporary stores. Private evidence: .local/transcript-universe-20260913/.
This is preparation and finite-scope evidence; actual start and outcome are
recorded separately.


Full-universe preparation completed at 2026-09-13T15:04:06.511867Z.
All 27,796 selected inputs passed; frozen plan
9836d71ad94b77166d320a3010bf447d29dfa796c82d3031624f6009993361d8
has an exact 27,796-call maximum and zero review calls. Automatic approval
review rejected the live launch before process creation because the current
user request did not explicitly authorize sending this expanded full raw
transcript payload to OpenAI. No workaround or indirect launch was attempted.
The prepared workload remains unchanged awaiting that explicit transfer approval.

Verified after rejection: zero request receipts for this plan; no start,
authorization, run-log or run-directory marker; all 234 previously stored
structured-output identities unchanged and zero newly stored outputs.
Evidence: .local/transcript-universe-20260913/launch-block.json and RUN_STATUS.md.
This is a blocked-before-launch result, not extraction completion.


### Full-universe transfer approval and launch - September 13, 2026 UTC

The user explicitly replied "approve" to sending the full 27,796 prepared
transcripts to OpenAI through the existing subscription, using at most 27,796
Terra/high calls and saving the outputs in the company database.
This resolves the prior automatic-approval launch block for the exact plan
9836d71ad94b77166d320a3010bf447d29dfa796c82d3031624f6009993361d8.
The original launch-block evidence remains preserved.

The finite background process launched at 2026-09-13T15:20:47.971472Z as Linux PID
998468. Three native extraction request receipts were confirmed
pending at startup, within the approved concurrency. This is evidence of a
running batch; it does not claim completed extraction or publication.
The unchanged cap, no-retry policy, failed-item continuation and incremental
database publisher apply. No recurring automation or provider refetch was added.

Approval and actual start evidence:
.local/transcript-universe-20260913/explicit-transfer-approval.json,
authorization.json, started.json and run.log.


Initial live publication verified at 2026-09-13T15:24:17.627244Z: the first nine new
structured outputs and nine automatic assessments were read back and matched
their original model bytes, structured JSON, canonical source linkage and
assessment evidence. New-table foreign-key checks passed; all 234 previous
structured outputs retained their semantic identities. The running progress
snapshot showed 13 stored outputs and
27783 remaining. This is initial-run validation, not full
population completion. Evidence:
.local/transcript-universe-20260913/initial-publication-audit.json.


## Luna/high extraction and five Astra/high reviews - September 13, 2026 UTC

The user requested stopping the Terra universe runner, storing all completed
outputs, switching extraction to Luna/high, and reviewing Luna against full raw
transcripts and the project's concise structured-call goal with Astra/high.
The user then selected five random unprocessed transcripts, at most five Luna
extractions plus five Astra reviews (10 calls total), no automatic retries,
and no full-universe Luna launch in this comparison.

Terra PID 998468 received a parent-only SIGINT at 15:48:34 UTC. Its three
in-flight workers finished; it exited with 122 completed outputs, all verified
against exact saved model bytes, JSON, source linkage and automatic assessments
in company.sqlite at 15:52:27 UTC. Total prior structured outputs: 356.
No completed output remains unpublished. Both structured tables passed foreign
key checks; the retained run status is stopped_by_user.

The new transcript_structured_call_luna_astra_prompt.v1 profile uses
gpt-5.6-luna/high for extraction, preserving the previous extraction instructions
and structured schema. New universe plans select Luna; frozen Terra plans retain
their exact model, prompt and request identity. Astra/high uses the tiered review
contract and explicitly assesses the original draft's usefulness, concision and
factual fidelity. Corrections are separate candidates and do not replace the
original extraction. The storage allowlist admits the exact new Luna profile;
no migration, shared schema, scheduler or credential mechanism changes.

The random sample is ESI 2024 Q1, ACAD 2021 Q1, LOW 2020 Q2, C 2022 Q3 and
AYI 2025 Q4, drawn from 27,673 unprocessed/unattempted ready entries in the
original frozen universe. Full saved sources and then source/draft pairs go to
OpenAI through the existing subscription transport. Each request has a
1,200-second deadline, three concurrent calls maximum and no retries.
Original Luna drafts and automatic assessments publish through the existing
replay-safe company publisher. Astra review evidence stays private for reporting.

53 focused offline checks passed with zero failures, errors or skips, covering
new profile/role enforcement, unchanged extraction inputs, exact Luna storage,
replay/no-repeat behavior and prior Terra/transport/storage compatibility.
Evidence and pinned preparation:
.local/transcript-luna-astra-20260913/. This records preparation and authority;
actual live outcomes are recorded after execution.


The five-source Luna/Astra live launch was blocked before process creation by
automatic approval review. The initial local permissions preflight was corrected
before any model request. A subsequent read-only proof confirmed all five full
sources exactly match the prior explicitly approved OpenAI-transfer universe;
automatic approval review still rejected the identical launch and requires a
fresh explicit payload/destination approval. No alternate transport, indirect
launch, repeat model call or workaround was attempted.

At the blocked handoff, both new plan IDs have zero request receipts, the pilot
start marker is absent, and no Luna/Astra quality result exists. Terra remains
stopped with all 122 completed results verified in the database (356 total).
Implementation and 53 focused checks are complete. Preserved evidence:
.local/transcript-luna-astra-20260913/blocked-result.json,
launch-block.json, existing-transfer-approval-proof.json,
launch-reconsideration-block.json and COMPARISON_STATUS.md.


### Explicit Luna/Astra transfer approval and start - September 13, 2026 UTC

The user replied "approve" to the exact five-full-transcript and generated-draft
transfer to OpenAI through the existing Codex subscription for five Luna/high
extractions and five Astra/high reviews. This resolves the prior launch block
for pilot 9727c687d3c7aae0168a0fd935f24b75a4f0303d4818e46dcc5529a95f15cb17; all rejection evidence remains preserved.
The unchanged pilot started at 2026-09-13T16:06:16.984873Z. The fixed maximum is 10 calls,
zero retries and no full-universe Luna run. This records start, not completion.
Evidence: .local/transcript-luna-astra-20260913/explicit-transfer-approval.json
and started.json.


### Luna/Astra sample completed with review limitations — September 13, 2026 UTC

The fixed pilot finished at 2026-09-13T16:11:44.775914Z: exactly five Luna/high extractions
and five Astra review calls, zero retries. All five original Luna outputs and
automatic assessments are verified in company.sqlite, 361 total. All 356 prior
outputs are preserved, and both structured tables passed foreign-key checks.
Every review is bound to its full frozen source and original Luna response.

Summaries contain 349–529 words versus 4390–8953
source words, a combined 6.3% length ratio. All five automatic results
are accepted_with_flags for source_metadata only; this is not factual approval.
All five raw reviews recommend targeted coverage/qualification/metric corrections.
Three review packages pass consistency checks; ACAD and C fail category/severity
consistency. Their raw judgments are retained, not promoted to validated reviews.

All five native Astra calls requested high but emitted fallback model metadata.
The CLI catalog lacks Astra, so effective reasoning effort is unverified.
This is not a verified Astra/high quality pass. No extra review, retry,
replacement extraction or full-universe Luna run was made. The ten-call approval
is consumed. Evidence: .local/transcript-luna-astra-20260913/QUALITY_REPORT.md,
aggregate-quality.json, completion-receipt.json, result.json, reviews/ and universe/.


## Astra/xhigh personal-project review of original Luna outputs - September 13, 2026 UTC

The user explicitly requested the same five Luna outputs be reviewed by
Astra/xhigh against their original transcripts, emphasizing personal-project
quality/value/cost, accurate cited numbers over exact quotation, and a final
verdict on Luna's quality. This authorizes one fresh bounded Astra/xhigh reviewer
session covering ESI, ACAD, LOW, C and AYI. There are no new extractions, native
CLI model calls, provider fetches, canonical-store accesses, retries or automatic
full-universe launch in this review.

The fresh default-role reviewer /root/astra_xhigh_luna_value_review was dispatched
with explicit gpt-6-astra and xhigh settings. It uses the app's delegated-model
route, rather than the legacy CLI whose Astra reasoning setting was unverified.
Inputs are exactly the five prior original Luna drafts and full source snapshots,
hash-verified against original exchange evidence, covering all 259 source turns.
Earlier review opinions and corrected candidates are excluded to avoid anchoring.

The reviewer must distinguish genuine numerical/meaning errors from paraphrasing,
missing speaker metadata, harmless shorthand, optional detail and editorial
preferences, while retaining decision-relevant qualifications and central themes.
It gives a final default-extractor recommendation and the minimum proportionate
quality checks. Only two private verdict artifacts may be written; original
sources, drafts, database contents and production configuration stay untouched.
Preparation/dispatch evidence:
.local/transcript-astra-xhigh-personal-review-20260913/input-manifest.json,
authorization.json and started.json. This entry records dispatch, not completion.


### Completed fresh Astra/xhigh personal-project review

Completed at 2026-09-13T16:37:29.073642Z. The fresh explicitly assigned gpt-6-astra/xhigh reviewer
read all 259 source turns (33,233 words) and the same five original Luna/high
outputs, checking 29 reported-result and 22 guidance entries. Its final verdict
is 1 usable as-is, 4 usable with minor caveats, 0 needing material correction,
and 0 unsuitable. It found no materially wrong financial number or misleading
meaning in the selected results and guidance under the user's concise
personal-research standard.

Astra recommends Luna/high as the default, with focused checks on consequential
figures and qualifications, local citation/wording repair, and stronger-model
review only for difficult or consequential cases. This is a five-case practical
judgment, not a population accuracy, measured cost/weekly quota, or matched
Terra comparison. The optional prompt adjustment was not implemented.

Parent verification passed: all five input bundle hashes and all 20 protected
original evidence hashes were unchanged, complete turn coverage and aggregate
counts reconciled, and 17 review pointers resolved. Both verdict files are
private mode 600. No new extraction, native CLI invocation, provider fetch,
canonical-store access, database publication, configuration change, or
full-universe restart occurred. The latest previously verified database total
remains 361; it was not re-queried for this review. Earlier review records remain
preserved as historical evidence.

Evidence: .local/transcript-astra-xhigh-personal-review-20260913/ASTRA_VERDICT.md,
astra-verdict.json, final-audit.json and completion-receipt.json.


## Resume remaining original transcript universe with Luna/high - September 13, 2026 UTC

The user explicitly requested resuming Luna for the remaining transcripts and
storing the outputs in the database after the successful five-case Astra/xhigh
review. This supersedes the earlier sample-only pause for this bounded remainder.
The prior full-transcript transfer approval remains recorded at
.local/transcript-universe-20260913/explicit-transfer-approval.json.

The locked immutable inventory at 2026-09-13T16:42:08.504075Z verified 361 stored
structured outputs. Of the original 27,796 selected quarters, 127 are now stored;
27,669 remain without a stored output. The resumed batch selects exactly 27,668
unattempted original source captures across 1,217 tickers. ADM 2026 Q2, which has
an earlier model-request receipt, is excluded from this no-retry launch. Newly
fetched transcripts outside the original frozen selection do not extend it.

The finite maximum is 27,668 Luna/high extraction calls through the existing
Codex ChatGPT subscription, sending full frozen source text and the unchanged
structured template to OpenAI. There are no additional model-review calls,
provider fetches, automatic retries, API billing fallback, or recurring-unit
changes. Concurrency remains three, with a 1,200-second per-request deadline.
Completed originals and automatic quality assessments publish incrementally
through the established replay-safe company.transcript.structured publisher in
the fixed company.sqlite store. Network work precedes each short write lock.

The existing nine pinned source files still match the five-case pilot baseline,
whose 53 focused tests passed. Database storage readiness was checked again.
The private preparation helper verifies original source/request bindings and
freezes versioned Luna request identities without changing the old Terra plan.
The runner retains its existing pause after three transport failures and on
input/publication failures. No quota credit reset or automated restart is added.

This entry records authorized preparation, not a successful launch.
Evidence: .local/transcript-luna-universe-20260913/inventory.json,
database-output-baseline.json, resume.py and preparation-progress.json.


### Luna remainder prepared; automatic approval launch block

All 27,668 original remainder inputs passed source/request verification at
2026-09-13T16:51:02.836995Z. Frozen Luna/high plan:
7e2dc1bbee29f32c8364038824651a7e35d8d6ed890d13514c51d65ff6f57c91.
The existing 53-test validated source pins still match. Preparation made no
model or provider call.

Automatic approval review rejected the live launch before process creation.
A read-only proof then verified the earlier explicit full-text transfer approval
and that every new input is an unchanged subset of the original 27,796-source
approved payload. The same launch was reconsidered with that evidence and
rejected again: automatic review still requires explicit trusted user approval
of the sensitive full-transcript payload to OpenAI for this Luna run. No bypass,
indirect launch, model request or database publication was attempted. The
prepared batch is intact, with no started.json or run.log created.

The remaining question is explicit approval to send the full text of 27,668
remaining transcripts to OpenAI through the existing Codex subscription for at
most 27,668 Luna/high calls and save their outputs in the company database.
No additional model reviews, retries, API fallback, provider calls or recurring
changes are included. Both launch rejections remain recorded.

Evidence: .local/transcript-luna-universe-20260913/prepared-plan.json,
preflight.json, launch-block.json, transfer-scope-proof.json,
launch-reconsideration-block.json and RUN_STATUS.md.


### Explicit Luna remainder transfer approval - September 13, 2026 UTC

The user replied "approve" to sending the full text of the prepared 27,668
remaining transcripts to OpenAI through the existing subscription, using at
most 27,668 Luna/high calls, and saving the outputs to the company database.
This is explicit transfer approval for the unchanged frozen plan
7e2dc1bbee29f32c8364038824651a7e35d8d6ed890d13514c51d65ff6f57c91.
The prior launch rejection evidence is preserved. The private launch helper now
requires this exact approval receipt before execution. Scope remains three
concurrent requests, zero automatic retries, zero model-review/provider calls,
and no API fallback or recurring-unit change. This entry records approval;
actual launch and initial publication must be verified separately.

Evidence: .local/transcript-luna-universe-20260913/explicit-transfer-approval.json.


### Luna remainder launched after explicit transfer approval

The unchanged Luna/high remainder plan
7e2dc1bbee29f32c8364038824651a7e35d8d6ed890d13514c51d65ff6f57c91
launched at 2026-09-13T16:54:38.885492Z as PID 1073725, after the user's explicit
transfer approval. The process is running the existing validated universe runner
through the private progress wrapper. The finite maximum remains 27,668
extraction calls for 1,217 tickers, concurrency three, no retries or extra review
calls. Its continuously updated readable status is
.local/transcript-luna-universe-20260913/RUN_STATUS.md.

This records a verified launch, not completion of the population. Initial
publication validation is recorded separately when an output completes.
Evidence: .local/transcript-luna-universe-20260913/started.json,
authorization.json, explicit-transfer-approval.json and launch-verification.json.


### Initial Luna remainder publications verified

At 2026-09-13T16:56:54.027823Z, the first three resumed Luna/high outputs and
automatic assessments passed locked immutable database verification. Exact
original model bytes, structured JSON, and source snapshots match their private
publication evidence; all three passed Luna runtime validation. The database
contained 364 structured outputs at that check, with all 361 prior semantic
identities preserved and zero foreign-key violations in both structured tables.

The finite batch remains running. This is an initial publication check, not
full-population completion. Progress, remaining count, and any pause are updated
in .local/transcript-luna-universe-20260913/RUN_STATUS.md and the pinned plan's
progress.json. No further model reviews or retries have been added.

Evidence: .local/transcript-luna-universe-20260913/initial-publication-audit.json
and handoff-status.json.


## User-requested 50-worker Luna continuation - September 13, 2026 UTC

The user explicitly requested increasing concurrent Luna extraction from three
to 50 workers. This supersedes the previous three-worker operational setting for
the already approved original Luna remainder. It does not add sources, calls,
reviews, retries, destinations, credentials or recurring execution.

The existing coordinator received parent-only SIGINT; its native model children
finished normally. All 22 completed outputs and their exact source/response
evidence were verified in the database, with 383 total outputs, all 361
pre-batch identities preserved, and zero structured foreign-key violations.
No request is repeated.

The runner now accepts a bounded execution-only concurrency override of 1–50.
Defaults and frozen plans remain unchanged; the same plan ID, source pages,
Luna/high prompt/schema, request identities and 27,668-call total cap are reused.
The new invocation has at most 27,646 unconsumed calls and 50 workers. Model
requests still precede short existing physical-store publication locks. The
existing three-transport-failure pause drains in-flight calls without retries.

All 50 focused offline tests passed in 65.799 seconds, including 50 overlapping
workers with temporary-store publication/replay, invalid bounds before access,
and draining 50 failed calls without starting remaining work or retrying.
The final concurrency-only diff was reviewed. Existing source pins are unchanged
except the bounded runner update; no registry, migration, provider, schema,
model prompt, shared locking primitive or native transport changed.

This entry records readiness for the requested increase, not a successful live
50-worker launch. Evidence: .local/transcript-luna-universe-20260913/concurrency-50/
before-increase.json, drain-request.json, drain-audit.json, validation.json,
source-before.json and fifty.py.


### 50-worker continuation running and initial publication verified

The same approved plan resumed at 2026-09-13T17:09:18.891804Z as PID 1087842.
At 17:09:55 UTC, 50 simultaneous native gpt-5.6-luna extraction processes were
observed, with 50 in-flight entries reported by the coordinator. Available
host memory at that observation was approximately 3,268 MB; the native
processes' combined proportional memory was approximately 793 MB.

At 2026-09-13T17:10:34.683250Z, 22 new outputs had been saved since the increase,
giving 405 database outputs. Five new outputs and their automatic assessments
were verified against exact original responses and source snapshots; all five
passed Luna/high runtime validation. All 383 outputs present before the
increase were preserved, with zero structured foreign-key violations.

Two early calls ended with a disconnected response stream/network decoding
error. Retained error events reported no explicit throttling or quota error.
Those attempts are not retried. The pool continued processing and publishing.

Latest handoff at 2026-09-13T17:13:18.369609Z: 125 outputs saved since the increase,
50-worker setting, status running, 2 failed calls recorded. The finite total
call cap remains 27,668, and no completed transcript is repeated. This is
ongoing batch execution, not full-population completion.

Evidence: .local/transcript-luna-universe-20260913/concurrency-50/started.json,
authorization.json, initial-running-snapshot.json, initial-publication-audit.json,
handoff-status.json and RUN_STATUS.md. The existing parent RUN_STATUS.md is
also updated continuously by the 50-worker progress callback.


## Explicit resume of paused Luna batch - September 13, 2026 UTC

After being told that the 50-worker run paused after three consecutive network
stream disconnects, the user requested "Can you resume now?" This authorizes
continuation of the same approved frozen Luna/high plan at 50 workers, selecting
only its 16,443 still-unattempted transcripts. The 11,152 successful and 73 failed
attempts are terminal and skipped. The original 27,668 total call maximum,
full-text transfer approval, subscription destination, model, prompt/schema and
database publisher remain unchanged; there are no retries or additional reviews.

Preflight verified that the previous coordinator exited, all 11,225 attempted
entries were settled, and the pending entries had no prior request receipt.
Every completed record's semantic identity was present in the database, with
11,513 total outputs and zero structured foreign-key violations. All validated
source pins still match the 50-test baseline; no production code change or
test rerun was necessary. The prior paused progress/result are preserved.

The private continuation directory is
.local/transcript-luna-universe-20260913/resume-20260913-evening/.
Its authorization.json, preflight.json and database-output-baseline.json record
this scope. Once launched, active-run.json at the parent and concurrency-50
directories identifies the latest invocation, without replacing historical
started.json or run.log evidence. Read the active pointer for subsequent status
checks. This entry records preparation and authorization, not a successful launch.


### Evening Luna checkpoint resume launched and publication verified

The user-authorized continuation launched at 2026-09-13T22:37:15.501292Z as
PID 4082042. Fifty simultaneous native Luna processes were observed at
22:37:53 UTC. It reuses the same plan and requests, skips all terminal
attempts, and publishes new structured outputs incrementally.

At 2026-09-13T22:38:36.141494Z, the first 21 new outputs were present in the
database, for 11,534 total outputs. All 11,513 pre-resume semantic identities
were preserved. Five new outputs and their assessments passed exact original
response/source and Luna/high runtime checks; structured foreign-key checks
returned zero violations. There were no new failed attempts at this audit.

The batch remains running, not complete. For status, read
.local/transcript-luna-universe-20260913/active-run.json for the current
invocation and use its log_path and script_path. Historical started.json and
run.log under concurrency-50 still identify the previous stopped invocation.
Live RUN_STATUS.md files are updated by the current callback.

Evidence: .local/transcript-luna-universe-20260913/resume-20260913-evening/
started.json, launch-verification.json, initial-publication-audit.json and
handoff-status.json.
