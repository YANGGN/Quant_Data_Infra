# Stage 12C — Market v1 Two-Session Gap Completion

**Status:** Implemented — bounded manual no-copy Market v1 two-session
completion, independently verified with the limitations recorded in the
[Stage 12C evidence record](STAGE12C_EVIDENCE.md).

**Decision dates:** 2026-08-15; credential-delivery refinement 2026-08-16
**Contract identifier:** `quant_data.stage12c_market_gap_v1` v1.0.0

## Purpose and authority

Stage 12C is the narrowly bounded successor to the completed Stage 12A
authority/path gate and the independently verified Stage 12B fixture-only
collector. It authorizes an additive, manual Market v1 completion for exactly
two new market sessions without making a second full database copy.

This contract does not reopen Stage 9, Stage 10, Stage 11, or Stage 12B. The
completed retained Stage 10 source remains immutable. It does not implement
the separately authorized [Stage 12D no-transfer adoption/freeze
contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md), Stage 12E scheduling,
a public consumer, or any broader activity.

The Stage 12C offline primary and independent gates completed with focused
35/35, compatibility 93/93, and full 611/611 passing tests (verification
evidence SHA-256
`8c0d7290e28173acbb3ed1930314daf2dd26357a2b9b1cbc6ba5d220ec93d4e5`).
The live completion, receipt, bounded provider outcomes, source-neutrality
method, and limitations are recorded in the
[Stage 12C evidence record](STAGE12C_EVIDENCE.md).

Related contracts and evidence:

- [Stage 12 Market v1 authority gate](STAGE12_MARKET_V1.md)
- [Stage 12A evidence](STAGE12_EVIDENCE.md)
- [Stage 12B incremental collector](STAGE12B_INCREMENTAL_MARKET_V1.md)
- [Stage 12B evidence](STAGE12B_EVIDENCE.md)
- [Stage 12C evidence](STAGE12C_EVIDENCE.md)
- [Stage 12D no-transfer adoption/freeze contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
- [Stage 10 market-history evidence](STAGE10_EVIDENCE.md)

## Frozen scope

The authority roster is exactly the frozen Stage 12A Market v1 roster of 629
instruments: 519 equities, 95 reviewed non-Russell ETFs, and 15 reviewed
major indexes. It is not rediscovered, extended, filtered, or otherwise
changed by Stage 12C.

The only requested inclusive date range is:

| Field | Fixed value |
| --- | --- |
| `from` | `2026-08-13` |
| `to` | `2026-08-14` |
| Expected sessions | `2026-08-13`, `2026-08-14` |
| Logical units | one request per frozen symbol; 629 total |

`AAPL` is the mandatory first sentinel. It must return a complete, valid
two-row response for both exact sessions before any later request. The
remaining 628 symbols proceed only in the reviewed sorted Stage 12A roster
order. No new instrument, historical gap, date, calendar inference, currency
conversion, adjusted-price inference, corporate-action inference, common
start-date claim, full-US/delisted-coverage claim, or historical constituent
membership claim is in scope.

## Source and target identity

Before a provider credential is read or any transport begins, the implementation
must prove that the explicit project-root-relative target
`data/market.sqlite` and the immutable retained Stage 10 source

`/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1/stores/market.sqlite`

are the same approved baseline. Each must initially have SHA-256
`b0ee0a02cc74e603320d0fa4f7a68a64339f8ed834229bdc480efa0351cc6f3c`, and
the preflight must prove exactly 4,235,893 current rows and 4,236,635 version
rows with zero missing current references, plus the retained Stage 10
capture-count, foreign-key, integrity, duplicate, and current-pointer checks
recorded in the retained evidence. A mismatch is a systemic stop condition.

The retained source is immutable and is the recovery baseline. Stage 12C is
not authorized to copy, replace, alter, compact, retire, or delete it. The
project-local target is the only approved database write target, and only after
all gates and local preflight have passed. This is not a new default-path
authorization for any other store or stage.

No second full database copy, database migration, schema change, default-path
fallback, or alias is authorized. The existing `0010` market
capture/version/current physical model remains the only persistence model.

The final source-neutrality checks used `mode=ro&immutable=1` only after a
zero-WAL precondition. A plain `mode=ro` read can update an existing `-shm`
sidecar timestamp even though it makes no canonical database change; that prior
metadata effect is disclosed in the evidence and is not treated as mutation.

## Provider request discipline

After complete local/static/baseline preflight and the offline and independent
gates have passed, and immediately before a permitted transport attempt, the
credential phase first uses a nonblank `FMP_API_KEY` from the process
environment. If that value is absent or blank, it may use only the reviewed
`quant_data.credentials.read_project_credential` helper to resolve only
`FMP_API_KEY` from the exact canonical project-root `.env` file. The helper may
not import other keys into `os.environ`, read an arbitrary dotenv file, expand
or interpolate variables or commands, or log, persist, echo, or otherwise
expose a secret. The fallback does not make `.env` a provider, scope, retry,
scheduler, or broader execution authorization. The credential must not appear
in a source file, receipt, sidecar, or command-line argument.

The first canonical invocation stopped at the former environment-only
credential lookup before any provider request, AAPL sentinel, project-local
data mutation, or retry. A separately authorized later canonical invocation
was not an automatic provider retry and completed Stage 12C. This decision
does not alter historical Stage 9-11 evidence or authorize a repeat of any
completed Stage 9-11 provider request.

Each logical unit has exactly one physical request, with no automatic or manual
retry under this Stage 12C run. Requests are serial, and the start of each
provider attempt is at least one second after the preceding provider attempt.
There is no network activity while a database write lock or transaction is
held.

The request is exactly:

```text
GET /stable/historical-price-eod/full?symbol=<frozen-symbol>&from=2026-08-13&to=2026-08-14
```

Only the `symbol`, `from`, and `to` query parameters are permitted. Redirects,
alternate endpoints, extra query parameters, pagination, batching, discovery,
or an inferred provider request are prohibited.

## Private durable sidecars and recovery evidence

The exact project-root-relative private state root is:

```text
data/.stage12/market-v1/stage12c-20260813-20260814/
```

It is not a database target. Its directories are created with mode `0700`; all
unit intent, raw-response spool, result, and resume files are mode `0600`,
created with `O_EXCL`, and durably fsynced. A sidecar must never contain the
FMP credential. A stable unit identity contains the frozen symbol, exact range,
endpoint, query, sequence number, and scope/roster bindings.

For every attempted unit, the implementation must durably record its intent
before transport. It must durably spool the raw response body before writing
response metadata, parser result, publication result, or resume advancement.
The receipt's observed `captured_at` is retained and reused on local replay;
replay does not issue another provider request. An issued intent without a
complete durable spool is ambiguous request state and is a systemic stop with
no retry.

The recovery procedure is limited to an offline rehearsal against a controlled
fixture store: start from the immutable retained-baseline equivalent and replay
the durable local spools without transport. It must not be performed against
the live project-local target under this contract. This recovery design does
not authorize target replacement or any destructive operation.

## Response admission and publication

An admissible publishable response is HTTP `200` with `application/json` and a
JSON list whose every row has exactly the ten frozen provider-native fields
shown below, with no additional fields:

| Field | Required type/constraint |
| --- | --- |
| `symbol` | exact requested frozen symbol |
| `date` | ISO date, exactly one of the two scoped sessions |
| `open`, `high`, `low`, `close` | finite provider-native numeric values |
| change, changePercent, vwap | finite provider-native JSON numbers |
| `volume` | non-negative integral provider-native value |

No row may be duplicated, outside the two scoped sessions, or omit a required
field. A complete response contains exactly two rows: one for `2026-08-13` and
one for `2026-08-14`. The `AAPL` sentinel must be complete; an empty, partial,
or missing-date AAPL response is a systemic stop and no later request may
begin.

For a later symbol, an exact nonempty complete two-row response may publish
through the new Stage 12C collector using the Stage 12B-equivalent
point-in-time, immutable capture, version/current-pointer, and exact semantic
no-write rules. Parsing completes before a database write lock is acquired.
Publication uses the existing `0010` tables only and may not invent timestamps
for date-only provider values.

For a later symbol, each of the following closes the unit only as an explicit,
private, disclosed noncoverage outcome in the sidecars; it creates no database
fact and receives no retry:

- a successful empty response;
- a successful response that is missing one scoped date or otherwise partial;
- an exact HTTP `404`, `410`, or `422` `application/json` response matching the
  reviewed FMP error envelope.

For the completed run only, the sealed HTTP `402` disposition in the
[Stage 12C evidence record](STAGE12C_EVIDENCE.md) is a further private
noncoverage outcome: ordinal 615 may replay only its exact sealed tuple, and
the remaining frozen-plan ordinals 617 through 629 may close only on the same
sealed raw-response fingerprint with the reviewed response constraints and
matching plan identity. It creates no database fact and is not a general HTTP
`402` policy.

The following are systemic stops: redirects; authentication or authorization
failure; every HTTP `402` outside that sealed disposition, HTTP `429`, or `5xx`;
transport failure; wrong MIME type; malformed JSON; an unreviewed error
envelope; a non-list 200 response; an out-of-range, duplicate, or schema-invalid
row; resource bound breach; lock, database, integrity, or persistence failure;
a baseline-identity mismatch; and any ambiguous issued request state. A
systemic failure may not advance the resume state, write a terminal outcome,
substitute an inferred value, or retry.

## Completion and evidence gates

Stage 12C may begin live provider work only after all of the following gates
are complete and their evidence is accepted by the primary and independently
reviewed:

1. **Offline contract gate.** Dependency-free fixture tests prove roster/date/
   sequence binding, endpoint and query exactness, one-attempt/no-retry
   behavior, pacing, credential isolation and named-only fallback, strict parser boundaries, terminal
   noncoverage handling, and zero-write failure paths.
2. **Target and source gate.** A controlled preflight proves explicit-root path
   resolution, the two matching initial SHA-256 values, retained Stage 10
   counts, integrity, duplicate, and current-pointer checks, and source
   neutrality before and after non-mutating checks.
3. **Durability and recovery gate.** Fixture tests prove `0700`/`0600` modes,
   exclusive creation, fsync ordering, no credential persistence, crash-state
   detection, stable receipt timestamps, and local replay without transport.
4. **Publication gate.** Explicit temporary fixture stores prove the existing
   `0010` capture/version/current behavior, point-in-time ordering, exact
   replay zero writes, no lock/network overlap, and no fact for terminal
   noncoverage.
5. **Independent gate.** An independent reviewer derives adversarial checks
   from this contract, inspects the implementation and evidence, and confirms
   that no default-path operation occurred before authorization.
6. **Live preflight gate.** Immediately before the first request, the operator
   repeats the approved local identity, path, source-neutrality, sidecar, and
   credential-presence checks. It first accepts a nonblank process-environment value and otherwise uses the reviewed named-only canonical-project `.env` helper. Failure stops before transport.

Private completion is recorded by the immutable receipt with SHA-256
`0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88`.
All 629 units closed: 619 published complete, three successful-empty
noncoverage outcomes, and seven sealed authorized HTTP 402 outcomes. AAPL
published both required sessions. The final project-local target has 4,237,131
current rows, 4,237,873 immutable versions, and 5,210 captures with clean
integrity, foreign-key, duplicate, and current-pointer checks. See the
[Stage 12C evidence record](STAGE12C_EVIDENCE.md) for the exact receipt,
sidecar, source-neutrality, and limitation bindings.

Completion does not transfer, replace, or otherwise adopt a database. Stage
12D is separately authorized for its no-transfer adoption/freeze contract and
offline implementation; it is not implemented or verified by this record.
Stage 12E remains closed.

## Explicit exclusions and nonclaims

Stage 12C does not authorize a new symbol or roster, dates outside
`2026-08-13` through `2026-08-14`, historical gap repair, full history reload,
provider retry, a second full database copy, migration, schema change, source
mutation, target replacement, deletion, retirement, promotion, transfer,
cutover, dashboard/tool/export exposure, public consumer access, or scheduler
work.

It does not claim a complete market universe, delisted coverage, historical
constituent membership, a common history start date, adjusted-price or
corporate-action inference, currency conversion, or an operationally proven
recovery procedure. Stage 12D is the separately authorized
[no-transfer adoption/freeze successor](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
and was not implemented by this Stage 12C record. Stage 12D later completed
under its [evidence record](STAGE12D_EVIDENCE.md); Stage 12E remains closed.
