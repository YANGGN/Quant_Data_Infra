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
store. The bounded Stage 8 implementation passed its primary offline fixture
gate and independent SolUltra verification. On 2026-08-14 the user explicitly
waived the unavailable in-app browser-automation check and accepted the bounded
Stage 8 exit gate as complete. This waiver is not evidence that the unrecorded
browser accessibility, responsive-layout, or runtime-network checks passed.

The user has authorized one bounded Stage 9 preparation: a manual FMP daily
OHLCV backfill for `SPY`, inclusive `2026-07-01` through `2026-07-31`, into
`/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607`. It is an additive,
market-only non-production slice with one request, no retry, and
`FMP_API_KEY` supplied only through the process environment. Its primary and
independent offline gates and bounded live population receipt have passed.
Promotion, old-store retirement, scheduling, and Atlas/tool/dashboard
exposure remain closed.

The user has now authorized a bounded Stage 10 FMP market-history population
under
`/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1`. Its
scope is the current provider-reconciled S&P 500, Nasdaq-100, and Dow 30
single-name roster, 95 reviewed non-Russell ETFs, and 15 reviewed major
indexes. It may fetch one symbol at a time from the full daily EOD endpoint,
retaining each symbol's earliest provider-returned OHLCV history without
inventing a common start date or historical constituent membership. Russell
2000 constituents, `^RUT`, `IWM`, automatic retries, scheduling, promotion,
public exposure, and destructive operations remain excluded. Live calls may
begin only after the complete Stage 10 offline and independent gates pass.
On 2026-08-12 the user further authorized an additive Stage 10 historical
extension for the same frozen 629-symbol roster. It may request the same full
daily EOD endpoint with exactly `symbol`, `from`, and `to`, using eight fixed,
inclusive, non-overlapping windows: seven five-calendar-year windows starting
`1990-01-01` and one final `2025-01-01` through `2026-08-12` window. This is a
successor candidate inside the same isolated non-production target; the
completed base Stage 10 receipt remains immutable. All 629 symbols are in
scope, including the eight prior broad-request failures, because these are new
explicitly dated requests rather than implicit retries. The required first
request is `AAPL` for `1990-01-01` through `1994-12-31`; it must prove the key's
older-history entitlement before the remaining requests begin. A successful
empty response is evidence only for a genuinely pre-listing window; the AAPL
sentinel may not be empty. Returned dates outside the requested window, ambiguous crash-after-request
state, automatic retry, scheduling, promotion, public exposure, and destructive
operations remain excluded. After the AAPL sentinel succeeds, only an exact
per-ticker HTTP 404, 410, or 422 application/json response with the reviewed
FMP error envelope may be recorded as a terminal failed window and skipped; the
final report must identify every failed ticker. Authentication, entitlement,
rate-limit, server, transport, malformed-response, and resource failures remain
systemic stop conditions, except that the exact sealed HTTP 402 response digest
encountered during this run is the operator-authorized per-window
entitlement-unavailable outcome. Live extension requests may begin only after
new offline and independent gates pass.

The retained Stage 10 base and extension receipts are complete and must not be
re-requested. The base records 629 symbols (621 completed and eight retained
failures). The extension closes all 5,032 planned windows as 3,970 complete,
1,004 successful empty responses, and 58 authorized terminal outcomes across
nine tickers. Both remain immutable, private, non-production candidates.
Current evidence does not retroactively prove the point-in-time ordering of the
pre-live independent gate; do not invent that historical proof.

After the Stage 10 price population completes, the user has also authorized a
bounded Stage 11 macro population in the macro store of that same isolated
non-production cohort. It is limited to BEA NIPA table `T10101` quarterly
history for series `A191RL`, table `T10105` quarterly history for series
`A191RC`, EIA monthly U.S. all-sector
electricity retail history for sales, revenue, price, and customers, and EIA
weekly series `PET.WCESTUS1.W`. `BEA_API_KEY` and `EIA_API_KEY` may be read
only from the process environment. Stage 11 requires dedicated non-fixture
relations, complete pagination, local-capture availability, one request at a
time, and serializes every exact logical BEA request, EIA retail page, and EIA
weekly request. Each logical unit may make one initial physical attempt plus at
most two automatic retries, only for connection/timeouts, HTTP 429, or HTTP
500-599. Backoff is deterministic and has no jitter: one second before the
first retry and two seconds before the second; every physical attempt observes
at least one second of pacing. Authentication/authorization, non-429 4xx
(including 408), redirects, wrong MIME, `200` provider-error envelopes,
byte/resource bounds, and parser/schema/configuration/publication/database/
integrity errors are never retried. No database write lock or transaction may
be held during calls or sleeps, and a failed physical attempt creates neither
evidence nor phase advancement. On success, existing `*_request_count` fields
count physical attempts, while scope `max_requests` remains a count of logical
units. Stage 11 also requires targeted database integrity and duplicate/current-
pointer checks, and a private candidate-only receipt. Routine completion trusts
successfully parsed provider responses and must not perform backup/restore or
full-corpus reconciliation; broader investigation is reserved for a detected
integrity anomaly. Immutable captures and versioned corrections must remain
available as system-observed vintages. A provider release timestamp or vintage
must be retained when supplied, but must not be invented when the source omits
it. Stage 11 does not authorize broader BEA/EIA discovery,
promotion, retirement, tools, dashboards, exports, or scheduling.

Stage 11 is complete only as a retained private, non-production candidate. Its
final resume state records `bea_published: true`, `retail_published: true`,
and `weekly_published: true`. The retained macro candidate has 635 BEA, 1,136
EIA retail, and 2,289 EIA weekly current rows with matching version counts;
targeted integrity, foreign-key, duplicate, and current-pointer checks passed,
and the private completion receipt exists. The final resume made no BEA or
retail request and one weekly request. None of the Stage 11 provider requests
may be repeated. The completed receipt remains pinned to registry `2.9.0`,
schema `1.7.0`, and source digest
`7e8ec6fc38d5a24962460d9c78a4df7754d3b29ad4b74c0c5e8ae807cd59ed56`;
the successor canonical registry revision only encodes the reviewed retry
policy and does not authorize another cohort. Promotion, retirement, tools,
dashboards, exports, scheduling, and public exposure remain closed.

Do not install, start, update, or remove a scheduler. Do not start a live
provider except the exact manual Stage 9, Stage 10, and Stage 11 slices above
after each slice's offline checks, independent verification, credential
preflight, dependency receipt, and exact-target checks pass. Those exceptions
permit only the reviewed Stage 11 transient retry policy above; otherwise,
they permit no retry, scope expansion, target, provider, promotion, or
store retirement. Do not host or deploy Atlas, connect it to operational
stores, select default paths, run live diagnostics, or perform destructive
operations.

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
  operations require the applicable roadmap gate and explicit authorization;
  the current exceptions are only the bounded manual Stage 9, Stage 10, and
  Stage 11 slices above, all non-production and candidate-only.

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
