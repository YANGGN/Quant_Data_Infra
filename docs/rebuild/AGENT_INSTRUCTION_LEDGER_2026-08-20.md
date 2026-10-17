# Quant Data Infrastructure Agent Instruction Ledger

Status: Frozen pre-compression snapshot from 2026-08-20. This file preserves
the former root instructions for audit and recovery; it is not an active source
of authorization. Use the current project-root `AGENTS.md`, the rebuild index,
and `CURRENT_OPERATING_ENVELOPE.md` for current instructions. Relative links
in the preserved body were originally resolved from the project root.

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

On 2026-08-15 the user selected `data/market.sqlite`, resolved from the
explicit project root, as the current canonical market default and authorized
bounded offline Stage 12A. Stage 12A may document and validate the strict,
explicit 629-symbol Market v1 roster, retained Stage 10 source-only receipt
bindings and coverage/non-claims, canonical registry revision `2.12.0`, exact
historical registry projections, and the ordered Stage 12B–12E decision gates.
It must remain dependency-free and must not open/create a SQLite database,
consult credentials, call a provider, repeat Stage 9–11 work, inspect retained
SQLite stores, copy/move/delete data, promote a candidate, expose a public
consumer, or install/start/update/remove a scheduler. The former current
default `data/market_data.sqlite` may appear only in exact historical registry
projections or historical evidence; it is not a current fallback or alias.
The user subsequently authorized bounded offline Stage 12B implementation,
defined by [the Stage 12B incremental collector contract](docs/rebuild/STAGE12B_INCREMENTAL_MARKET_V1.md).
That dependency-free injected-fixture collector is implemented and
independently verified; [Stage 12B evidence](docs/rebuild/STAGE12B_EVIDENCE.md)
records the result. It uses registry `2.13.0`, schema `1.8.0`, and the
existing `0010` market capture/version/current physical model solely in
explicit temporary fixture stores. It may not use a live provider, API key,
environment configuration, network, default or retained store, or new
migration; it may not promote, cut over, expose a public consumer, or
install/start/update/remove a scheduler.
Stage 12B does not reopen or repeat Stage 9–11 work. On 2026-08-15 the user
explicitly authorized the refined Stage 12C approach without another full
database copy. Stage 12C completed exactly the two sessions `2026-08-13` and
`2026-08-14` for the frozen 629-symbol roster in the existing project-local
`data/market.sqlite`, after proving the retained Stage 10 baseline identity.
`AAPL` was the mandatory first sentinel and all units followed the reviewed
sorted roster. The first canonical invocation stopped at the former
environment-only credential lookup before a provider request, sentinel,
database mutation, or retry. A separately authorized later invocation was not
an automatic retry and completed the slice. The immutable
[Stage 12C evidence](docs/rebuild/STAGE12C_EVIDENCE.md) binds registry
`2.14.0`/schema `1.8.0`, execution-plan SHA-256
`5e5c07f03313c1a0d3d3980fa8a900973a301664fecf84e6d37ab3c2f38a92f1`,
semantic-scope SHA-256
`2c11fd5bfe99160e7949db3a87f2e3b1616a829b975424df40ec7f4fb16d5392`,
and final private-receipt SHA-256
`0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88`.
It records 629 contiguous one-attempt chains: 619 published complete, three
successful-empty noncoverage outcomes, and seven narrowly authorized HTTP 402
noncoverage outcomes, with no database fact for any terminal outcome. The
final target has 4,237,131 current rows, 4,237,873 immutable versions, and
5,210 captures with clean integrity, foreign-key, duplicate, and
current-pointer checks. The retained Stage 10 market database remains
immutable and is the recovery baseline.

HTTP 402 was not generally reclassified. Only the exact ordinal-615 tuple
recorded in the Stage 12C evidence is authorized for local replay; for the
remaining frozen-plan ordinals 617 through 629, only the same sealed raw
response fingerprint with the reviewed response constraints and matching plan
identity is authorized. Every other HTTP 402 remains systemic and
non-advancing. Every issued request retains its intent and raw response so a
complete sealed response can replay locally without a second provider request;
ambiguous request state remains a stop condition. Final source-neutral checks
used `mode=ro&immutable=1` after a zero-WAL precondition. An earlier ordinary
read could update a `-shm` sidecar timestamp; that disclosed metadata effect is
not a canonical database mutation.

Stage 12C authorizes no new symbol, broader date range, migration, second full
database copy, destructive action, public exposure, scheduler, or repeat of
its completed provider work. Stage 12D is complete and independently verified
under its no-transfer project-local adoption/freeze contract and immutable
[evidence record](docs/rebuild/STAGE12D_EVIDENCE.md). Exactly two canonical
read-only proofs produced distinct immutable receipts with the same semantic
proof and left the main database, WAL, SHM, and rollback-journal stamps
unchanged. The independent reconciliation did not reopen SQLite or compute a
new full hash of the 6,589,505,536-byte database, and it is not a backup or
recovery proof. Stage 12D created no second database, copy, move, replacement,
backup, migration, registry bump, promotion pointer, provider/network access,
public exposure, or scheduler change. Registry `2.15.0`/schema `1.8.0` is the
historical path-aligned declaration for `data/market.sqlite`,
`data/macro.sqlite`, `data/company.sqlite`, and `data/news.sqlite`; its exact
historical projection restores Stage 12C/D registry `2.14.0`. The path update
copied, renamed, opened, or mutated no database. Stage 12E remains closed.

On 2026-08-17 the user explicitly authorized the canonical macro store's first
official GDP/CPI vintage population followed by automatic refresh for those
same series only. Registry `2.16.0`/schema `1.8.0` adds migration
`macro:0013_live_gdp_cpi_vintages`, two private datasets, and the fixed BEA/BLS
collectors. The completed backfill contains one BEA GDP Vintage History
workbook, 14 BLS annual CPI revision archives, and one current BLS API capture:
16 immutable captures, four series, 2,136 release identities, 4,012 immutable
versions, and 640 current observations. GDP covers `2002Q1` through `2026Q2`;
CPI covers `2008-01` through `2026-07`, preserving the source's unavailable
`2025-10` marker as no numeric fact. Integrity, foreign keys, raw-response
hashes, duplicate checks, and current pointers passed; every clean initial
version has correction sequence one. A repeated refresh returned two semantic
no-ops and zero writes. The active user timer
`quant-data-macro-vintages.timer` runs at 09:05 America/New_York on weekdays,
is non-persistent, uses no credential, makes exactly one BEA and one BLS
request, performs no retry or migration, and writes only changed normalized
facts. The 14-file historical backfill is complete and must not be repeated;
the timer does not fetch those archives. The exact `2.16.0` historical
projection restores registry `2.15.0` and removes this migration and its four
declarations.

The user then authorized the canonical macro store's bounded employment-
vintage slice. Registry `2.17.0`/schema `1.8.0` adds migration
`macro:0014_live_employment_vintages` and two private collectors without adding
a dataset or public consumer. The completed one-time backfill made exactly
three requests: Philadelphia Fed RTDSM payroll and unemployment workbooks plus
one two-series BLS current API request. The canonical store now contains 19
captures, six series, 3,120 releases, 18,461 immutable versions, and 2,633
current observations. Employment contributes 984 releases, 14,449 versions,
and 1,993 current observations: payroll spans `1939-01` through `2026-07` and
unemployment spans `1948-01` through `2026-07`. Philadelphia Fed source
vintage labels are retained, exact release dates are not invented, and their
availability remains local capture. Independent verification found clean
integrity, foreign keys, raw/value hashes, lineage, duplicates, correction
sequences, and current pointers. The pre-employment recovery backup is
`data/.macro-backups/macro.sqlite.pre-employment-20260817T1038-0400`, SHA-256
`4ab67e1857d4d2d1dc86d6efd8501c42d3f7835547229aa227741fa4a37b260a`.
The active non-persistent `quant-data-employment-vintages.timer` runs on the
first Friday of each month at 10:05 America/New_York and makes only the one
credential-free BLS current request. It performs no retry, migration, or
historical-workbook refetch; an outside-window service check issued zero
requests. The historical employment backfill is complete and must not be
repeated. The exact `2.17.0` historical projection restores registry `2.16.0`
and removes only migration 0014 and the two employment collectors.

The user then authorized a one-time extension of the canonical macro history
and local adoption of the already-complete retained Stage 11 weekly crude
cohort. Registry `2.18.0`/schema `1.8.0` adds migration
`macro:0015_live_macro_history_extension` and two manual-only collectors,
without a new dataset, credential, public consumer, or scheduler. The history
run reused five sealed responses and made exactly six new credential-free BLS
requests. It added 11 captures, two source-native Philadelphia Fed output
series, 1,165 releases, 14,577 immutable versions, and 1,980 current
observations. The official-vintage totals are now 30 captures, eight series,
4,285 releases, 33,038 versions, and 4,613 current observations. BLS all-items
CPI now reaches `1947-01` and core CPI reaches `1957-01`; `NOUTPUT` and
`ROUTPUT` remain separate GNP/GDP level series and do not reinterpret the BEA
GDP series. Philadelphia Fed vintage labels are retained with no invented
release date and local-capture availability.

The local-only weekly adoption made no provider request and copied the sealed
Stage 11 lineage exactly: one capture and 2,289 versions/current rows spanning
`1982-08-20` through `2026-08-07`, bound by bundle SHA-256
`d99195d5b9b2f9ded9243d383677a63c61f77952edfe07b740e7a3fc4d701f8f`.
The pre-change backup is
`data/.macro-backups/macro.sqlite.pre-history-extension-20260817T1243-0400`,
SHA-256 `5fbf583e24ad62e146c850797e39140a204e018ae029e5f39137381f3fcbd58d`.
Independent immutable verification passed. This one-time history extension
and adoption are complete and must not be repeated. The exact `2.18.0`
projection restores byte-exact registry `2.17.0`; the two existing macro
timers remain the only macro scheduling exceptions.

Registry `2.19.0`/schema `1.8.0` adds only the manual-only
`fmp.macro.gdp_cpi_release_calendar_history` collector. It reuses
`fixture.macro.economic_calendar` and the existing `macro.official_vintages`
tool dependency; it adds no migration, dataset, job, scheduler, or physical
surprise table. The completed manual FMP history used exactly 56 contiguous
windows of at most 90 days from `2013-01-01` through `2026-08-17` and retained
941 FMP events and 941 versions: 216 core MoM, 217 core YoY, 214 headline MoM,
217 headline YoY, and 77 GDP. Historical consensus availability is the explicit
`event_at_utc` assumption. The reviewed target alias map is exact, with no fuzzy
target matching, and ignores bare CPI, GDP Price Index, and GDP Consumer Spending labels. GDP
surprise is calculated on demand as one best-available record per quarter. It
prefers an exact-date BEA first release (`advance`, or source-native `initial`);
when that consensus is absent, it selects an exact-date `second`, then
exact-date `third` release. Every selection compares FMP consensus with the BEA
actual from the same release date and stage; FMP GDP actual is never used.
Equivalent reviewed aliases on the same date collapse only when their values
agree; conflicts fail closed. Later-stage selections are explicitly marked
`is_fallback`. The canonical read returns all 55 quarters from `2012Q4`
through `2026Q2`: 10 advance, one initial, 10 second, and 34 third; 54 have
numeric surprises and `2012Q4` is `missing_consensus`. CPI has 864 unchanged
results and uses actual from the same FMP event version. This derived mapping adds no
migration, physical surprise table, provider request, or scheduler. The pre-run
backup `data/.macro-backups/macro.sqlite.pre-fmp-consensus-20260817T1507-0400`
has SHA-256 `c257c332b83682d82dde921964a0000edb4a9250ea118831c046a1c63dfcad6d`
and mode `0600`; integrity, foreign-key, lineage, and duplicate checks are
clean. This manual history is complete and must not be repeated. No timer was
installed, the two existing macro timers remain unchanged, and the exact
`2.19.0` historical projection restores `2.18.0`.

The user subsequently authorized the code path for one fixed incremental FMP
calendar refresh and the payroll/unemployment surprise extension. The refresh
is bounded to 08:15 and 08:45 America/New_York on weekdays, one FMP request,
and no retry. After the user expressly accepted the credential/network/write
and additional-scheduler risk, the reviewed non-persistent unit was installed,
enabled, and started on 2026-08-18. It runs those two weekday times. The same
response is parsed independently for the existing GDP/CPI
kinds and the reviewed employment kinds; both publishers retain independent
semantic replay and lineage. Registry `2.20.0`/schema `1.8.0` adds only the
manual declaration `fmp.macro.employment_release_calendar_refresh` and its
reciprocal binding to the existing economic-calendar dataset. It adds no
migration, dataset, registry job, physical surprise table, or public route,
and its exact historical projection restores byte-exact registry `2.19.0`.

Payroll and unemployment surprises are calculated on demand. FMP supplies
consensus only; its actual is comparison-only. Payroll uses the official
employment target level minus the independently lineaged prior-month level,
while unemployment uses the official rate. A same-day BLS capture is preferred;
otherwise the first Philadelphia Fed RTDSM vintage is an explicitly labeled
proxy.

Registry `2.21.0`/schema `1.8.0` adds migration
`macro:0016_fmp_calendar_wholesale_evidence`, the private
`macro.fmp.economic_calendar_evidence` dataset, and one manual-only wholesale
collector. The completed one-time backfill made exactly 56 requests for the
same contiguous 2013-01-01 through 2026-08-17 windows and retained every
bounded response byte plus all 42,890 returned rows in 56 immutable captures.
The pre-run recovery backup is
`data/.macro-backups/macro.sqlite.pre-fmp-wholesale-20260818T1918-0400`,
mode `0600`, SHA-256
`44d4420cb2569f792bf19489febcf68a16c2d3916cad9893905c31aee681269b`.

Local replay made zero provider requests and normalized 323 employment
events/versions: 161 payroll and 162 unemployment. On-demand official-vintage
mapping returns 310 surprises spanning 2013 through 2026: 156 payroll and 154
unemployment, with 308 `ok` and two `missing_consensus`. The December 2025
same-timestamp October/November payroll pair remains fully retained as raw
evidence but is deliberately omitted from the older normalized identity, which
has no reference-period field. Integrity, foreign-key, raw/row hash, membership,
and run/artifact/snapshot lineage checks are clean. A second local replay made
zero requests and zero writes. Registry `2.21.0` projects byte-exactly to
`2.20.0`. This history must not be repeated; it reused the existing reviewed
FMP refresh timer and added no further timer, public route, physical surprise
table, or caller-selected database path.

The bounded dependency-free Stage 12A gate was implemented and independently
verified on 2026-08-15; [Stage 12 evidence](docs/rebuild/STAGE12_EVIDENCE.md)
records the completed immutable authority/path gate. It does not itself
authorize database operation or Stage 12B–12E activity. Stage 12B was
authorized separately and is complete only within the temporary fixture-store
boundary above.

Do not install, start, update, or remove another scheduler. Do not start or
repeat a live provider run outside the three active fixed macro refreshes above.
The GDP/CPI timer may fetch only its current BEA/BLS inputs; the employment
timer may fetch only its two-series BLS current input inside its release
window. The FMP calendar timer may make only its one bounded current-window
request at 08:15 and 08:45 America/New_York on weekdays, with no retry; it must
retain wholesale raw evidence before local normalization. The historical exceptions are the
completed bounded Stage 9, Stage 10, Stage 11, Stage 12C, GDP/CPI vintage,
employment-vintage, macro-history, FMP GDP/CPI release-calendar, and FMP
wholesale-calendar backfills plus the completed local weekly adoption;
none authorizes a repeat, scope expansion, different target, provider change,
promotion, or store retirement.
Stage 12D has no provider or credential authority. Do not
host or deploy Atlas, connect it to operational stores, run live diagnostics,
or perform destructive operations. The completed Stage 12C slice is the sole
reviewed write to `data/market.sqlite`; no other default-path operation is
authorized.

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

## Default fast path

The default workflow for ordinary bounded development is the
[fast path](docs/rebuild/FAST_PATH_DEVELOPMENT.md): one owner, one direct
implementation pass, focused validation, one final diff review, and a concise
handoff.

Timebox planning and decomposition for an ordinary task to 15 minutes. Do not
create subagents, new stage contracts, evidence records, full-suite runs, or
repeated verifier cycles for a local, reversible change covered by focused
tests. Update existing documentation once after the behavior works.

Use the heavier parallel workflow only when an accepted contract requires it
or the work changes a migration, registry, shared schema, cross-store
invariant, security boundary, live provider, credential path, deployment,
scheduler, public exposure, destructive operation, or canonical-data safety
boundary. Two genuinely independent workstreams may also justify delegation
when they materially reduce elapsed time. Use only the agents and gates needed
for the identified risk.

If the fast path cannot proceed safely, state the exact blocker and escalate
once. Do not invent additional phases or documents.

## Subagent task contract

When the exception workflow requires delegation, every delegated task must
state:

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

This is an exception workflow, not the project default. Use it only when the
[fast-path escalation criteria](docs/rebuild/FAST_PATH_DEVELOPMENT.md) or an
accepted contract require it:

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

Run the narrowest meaningful validation first. Expand to adjacent tests only
when the changed interface has adjacent consumers. Run the full suite only for
plausibly broad regressions, a required exit gate, or an explicit user request.

Routine local and reversible changes do not require a separate verifier after
focused validation and final diff review. When an accepted contract or the
fast-path escalation criteria require independent verification,
implementation workers test the behavior they own but do not approve the gate.
The `verifier` must:

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
  the only completed provider exceptions are the bounded manual Stage 9,
  Stage 10, Stage 11, and Stage 12C slices above. Stage 12C is the sole
  reviewed write to the project-local market default; Stage 12D completed only
  the independently verified no-transfer, no-provider read-only adoption proof.

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
