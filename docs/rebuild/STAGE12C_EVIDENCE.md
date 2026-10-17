# Stage 12C Evidence Record

Status: **Implemented — bounded manual no-copy Market v1 two-session
completion.** The private completion receipt is retained. Stage 12D authority
is limited to a separately gated refined no-transfer adoption/freeze; Stage
12E remains closed.

Verification and live-completion date: 2026-08-16

## 1. Purpose, authority, and boundary

Stage 12C completed exactly two new Market v1 sessions, `2026-08-13` and
`2026-08-14`, for the frozen 629-symbol roster. It used the existing
project-local `data/market.sqlite` only after proving it was the approved
retained Stage 10 baseline. It did not make a second full database copy.

The authority for this work consisted of the accepted Stage 12C contract and
the explicit user decisions to use the refined no-copy approach, use the
named-only canonical-project `.env` fallback when the process value is blank,
and continue under the narrowly sealed HTTP 402 disposition described below.
The earlier canonical invocation stopped at the former environment-only
credential lookup before a provider request, AAPL sentinel, database mutation,
or retry. The later canonical invocation was separately authorized; it was not
an automatic provider retry.

The retained Stage 10 source at
`/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1/stores/market.sqlite`
remained the immutable recovery baseline. No source copy, replacement,
migration, schema change, broad backfill, provider discovery, public consumer,
promotion, scheduler action, or destructive storage operation occurred.

## 2. Frozen bindings and target identity

| Binding | Retained value |
| --- | --- |
| Canonical market target | project-root-relative `data/market.sqlite` |
| Registry / schema | revision `2.14.0`, schema `1.8.0` |
| Stage 12C raw scope SHA-256 | `24d8448c124cedb5deb745b50943291d955f05e7a95ac1d9747a7d51b6760226` |
| Stage 12C semantic scope SHA-256 | `2c11fd5bfe99160e7949db3a87f2e3b1616a829b975424df40ec7f4fb16d5392` |
| Frozen execution-plan SHA-256 | `5e5c07f03313c1a0d3d3980fa8a900973a301664fecf84e6d37ab3c2f38a92f1` |
| Final private receipt SHA-256 | `0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88` |
| Approved pre-run source/target baseline SHA-256 | `b0ee0a02cc74e603320d0fa4f7a68a64339f8ed834229bdc480efa0351cc6f3c` |
| Initial matching canonical current/version rows | `4,235,893` / `4,236,635` |

The Stage 12C plan bound one serial logical unit to each frozen symbol. The
only requested dates were the two exact sessions above. AAPL was ordinal 1 and
published its exact two-session response before any subsequent provider call.
The remaining plan proceeded in its reviewed sorted roster order. No
instrument, date, endpoint, or query parameter was added.

## 3. Offline and independent gates

Offline and independent implementation evidence was completed before the live
slice. These checks used explicit temporary fixtures and did not contact the
provider, read a live/default store, or require a credential.

| Gate | Retained result |
| --- | --- |
| Stage 12C focused contract checks | 35/35 passed |
| Stage 12C compatibility checks | 93/93 passed |
| Complete dependency-free suite | 611/611 passed |
| Offline independent-verification evidence SHA-256 | `8c0d7290e28173acbb3ed1930314daf2dd26357a2b9b1cbc6ba5d220ec93d4e5` |
| Named-only `.env` fallback focused credential/operations checks | 33/33 passed |
| Additional credential boundary checks | 10/10 passed |
| Credential compatibility checks | 83/83 passed |

The credential helper first prefers a nonblank process `FMP_API_KEY`; only a
blank or absent value permits a lookup of that one name in the exact canonical
project-root `.env`. It neither imports the value into `os.environ` nor
expands/interpolates dotenv content, reads an arbitrary dotenv path, or
records a secret in sidecars or reports. It is reached only after local,
static, source, and sidecar preflight and immediately before a permitted
transport attempt.

Independent review also exercised the sealed local-replay path and its
mismatch matrix: changed plan, ordinal, symbol, range, unit identity, intent,
status, MIME type, redirect flag, byte count, raw digest, metadata-spool
digest, or an otherwise well-formed different HTTP 402 remained a systemic
stop with no database fact.

## 4. Live execution ledger

The final private journal records 629 unique, contiguous, one-attempt unit
chains. It contains no automatic retry. The complete ledger is:

| Outcome | Units | Details |
| --- | ---: | --- |
| Published complete | 619 | 1,238 Market v1 rows, two exact sessions per published symbol |
| Successful empty noncoverage | 3 | ordinal 177 `EA`, ordinal 201 `EUV`, ordinal 302 `IRBO` |
| Sealed authorized HTTP 402 noncoverage | 7 | ordinals 615 `^AXJO`, 617 `^FCHI`, 619 `^GDAXI`, 621 `^GSPTSE`, 624 `^KS11`, 626 `^NDX`, and 628 `^TWII` |
| Total closed | 629 | all frozen plan units, in contiguous ordinal order |

The mandatory AAPL sentinel at ordinal 1 published both required sessions.
`^DJI` at ordinal 616 also published its complete two-session result after the
local replay of ordinal 615. The three successful-empty and seven authorized
HTTP 402 outcomes produced no canonical database fact.

Every issued attempt has a durable intent, raw-response spool, response
metadata, result, and resume chain. The runner wrote the intent before
transport and the raw response before metadata or parser/publication outcome.
This permits local replay of a complete sealed response without repeating its
provider request. The completion receipt records the plan, outcomes, sidecar
bindings, target checks, and final database checks.

## 5. Exact sealed HTTP 402 authority

HTTP 402 was not generally reclassified as noncoverage. The authority is a
narrow, reproducible disposition rooted in the first sealed response:

| Field | Exact ordinal-615 value |
| --- | --- |
| Frozen plan unit | `^AXJO`, ordinal 615, `2026-08-13` through `2026-08-14` |
| Stable unit identity | `stage12c-97582963f362dc4a7c8f41bd579f0975` |
| Durable intent SHA-256 | `d4638e71d1277279ab5e7d8d8208839fc7806e70400a9fd90f6a6713d5770137` |
| HTTP status | `402` |
| Content type | `application/json; charset=utf-8` |
| Redirect | `false` |
| Raw response size | `215` bytes |
| Raw response SHA-256 | `38e6a6ea2ed189c5d4cab610c93eefc962b31fffdae06dd65390b90d7c0cff7c` |
| Canonical response-metadata spool SHA-256 | `e65c1a9f7cb51ebfcd5702145836612d8d1e5d02de4c79e79ef2849d3e84e5de` |

The ordinal-615 response was replayed locally from its complete durable spool;
it did not cause another provider call or a database write. For the remaining
validated frozen-plan ordinals 617 through 629, only the same sealed raw
response fingerprint, together with the reviewed response constraints and
matching plan identity, was eligible for the explicit private
`noncoverage_http_402_authorized` outcome. This allowed the six later matched
index units listed in the ledger to close. Every other HTTP 402, including a
different response body or a mismatch in any sealed field, remained systemic
and non-advancing.

## 6. Final database and source-neutrality evidence

The final database state is recorded by the immutable private receipt:

| Check | Final result |
| --- | --- |
| `market_price_current` rows | `4,237,131` |
| `market_price_versions` rows | `4,237,873` |
| Market captures | `5,210` |
| Stage 12C captures | `619` |
| Stage 12C current/version facts | `1,238` / `1,238` |
| Integrity check | `ok` |
| Foreign-key violations | `0` |
| Duplicate current facts / current-pointer anomalies | `0` / `0` |
| Terminal database facts | `0` |
| Availability policy | `available_at = captured_at` |
| Correction / supersession state | correction sequence `1`; supersession `NULL` |

All Stage 12C publications are initial facts for the two new sessions: they
have correction sequence one and no superseded predecessor. The retained
source's recorded identity and stat evidence were unchanged from the
source-neutrality receipt.

The final neutrality method deliberately avoids a SQLite side effect observed
with plain `mode=ro`: opening a source with an existing shared-memory sidecar
can update the `-shm` sidecar timestamp even when no canonical database content
changes. After proving the zero-WAL precondition, final source checks used
`mode=ro&immutable=1`, which avoids that sidecar interaction. The previously
verified multi-gigabyte baseline SHA-256 values were trusted for this final
receipt and were not recomputed as full roughly-six-gigabyte hashes.

## 7. Limitations and nonclaims

This record proves the reviewed runner's durable one-attempt journal and its
bound provider outcomes. It cannot prove that no request was made outside that
journal by an independent actor or process. The aggregate directory digest is
a commitment to the receipt's enumerated state; it is not an external
attestation, a complete filesystem snapshot, or proof excluding unjournaled
external activity.

Stage 12C does not establish a broader market universe, delisted coverage,
historical constituent membership, common history start date, adjusted-price
or corporate-action inference, a general HTTP 402 policy, provider rights,
full operational recovery rehearsal against the project-local target, public
consumer access, promotion, cutover, export/dashboard exposure, or scheduling.
It did not reopen or repeat completed Stage 9, Stage 10, or Stage 11 provider
work.

## 8. Gate disposition

Stage 12C is **complete** as the exact private, bounded no-copy two-session
Market v1 population recorded above. This evidence does not itself transfer,
replace, or otherwise adopt a database. Any Stage 12D work is limited to the
refined no-transfer adoption/freeze boundary and requires its own executable
evidence. Stage 12E remains closed; no scheduler has been installed, changed,
or started.

## Related contracts

- [Stage 12C two-session Market v1 gap contract](STAGE12C_MARKET_GAP_V1.md)
- [Stage 12B evidence record](STAGE12B_EVIDENCE.md)
- [Stage 12A evidence record](STAGE12_EVIDENCE.md)
- [Stage 10 evidence record](STAGE10_EVIDENCE.md)
- [ADR 0009: canonical market operational path](../adr/0009-canonical-market-operational-path.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
- [Scheduling and physical-store locking](SCHEDULING_AND_LOCKING.md)
- [Rebuild test strategy](TEST_STRATEGY.md)
