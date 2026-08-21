# Stage 12D Evidence -- Project-Local Market Adoption Without Transfer

## Outcome

Stage 12D is complete and the existing project-local market database is adopted
as the canonical frozen target under the bounded no-transfer contract.

The two authorized canonical proof invocations completed successfully. They
used the same logical proof, produced distinct immutable receipts, and left the
main database and every recorded sidecar stamp exactly unchanged. This is an
adoption/freeze result only. It is not a database transfer, promotion, backup,
recovery rehearsal, public release, or scheduler authorization.

## Authority and rationale

The user separately authorized Stage 12D after Stage 12C completed in the
already-approved canonical target:

    data/market.sqlite

That target was already approximately 6 GB. Creating another full database
would add transfer and storage risk without adding an approved data outcome.
ADR 0010 therefore replaced only the obsolete transfer consequence of the
earlier path decision with a no-transfer proof.

Stage 12D performed no copy, move, replacement, promotion, backup, restore,
migration, registry change, retained-source reopen, provider request,
credential read, network activity, public-consumer exposure, or scheduler
operation. Registry 2.14.0 and schema 1.8.0 remained unchanged.

## Frozen bindings

| Evidence field | Exact value |
| --- | --- |
| Contract | quant_data.stage12d_market_no_transfer_adoption_v1 |
| Contract version | 1.0.0 |
| Canonical target | data/market.sqlite |
| Registry / schema | 2.14.0 / 1.8.0 |
| Stage 12D raw scope SHA-256 | 2b20ad0cc5a62e12839c33cae81f3ef60e222535009df1caeda8ab2f93933e1e |
| Stage 12D semantic scope SHA-256 | 83ff9be19997eb77c9af025b439adf9a977a42113ab8ea413125a1b88f77c981 |
| Stage 12C completion receipt SHA-256 | 0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88 |
| Stage 12C execution-plan SHA-256 | 5e5c07f03313c1a0d3d3980fa8a900973a301664fecf84e6d37ab3c2f38a92f1 |
| Stage 12C semantic scope SHA-256 | 2c11fd5bfe99160e7949db3a87f2e3b1616a829b975424df40ec7f4fb16d5392 |
| Stage 12C ledger | 619 published, 3 successful-empty, 7 authorized HTTP 402, 629 closed |
| Canonical target identity SHA-256 | 5a7693962ccf3cc2ce5197bd2e4839b35f5a9c9cbf525d0bb96b4c2d4c65eed4 |

The Stage 12D loader accepted only the byte-exact scope source and its frozen
semantic digest. Unknown or missing keys, type/range changes, path or policy
drift, count-math drift, digest drift, and forged typed scope values failed
closed.

## Offline implementation and verification gates

All implementation and reliability tests used explicit synthetic roots beneath
the system temporary directory. They did not open the canonical database,
retained Stage 10 source, credentials, a provider, the network, or a scheduler.

The final relevant results were:

| Gate | Final result |
| --- | --- |
| Focused operations suite | 22 of 22 passed |
| Integration suite | 5 of 5 passed |
| Scope-loader suite | 5 of 5 passed |
| Bounded repository Stage 12D gate | 32 of 32 passed |
| Independent reliability/adversarial verification | 31 of 31 passed |

The 22-operation result is a component of the 32-test bounded repository gate,
not an additional cumulative count. The independent 31-check reliability run
was a separate adversarial verification gate.

The covered failures included scope and completion forgery, raw-file versus
embedded receipt digest confusion, the obsolete contract-inclusive completion
digest, target and receipt replacement at proof boundaries, symlinks and hard
links, nonzero WAL or rollback journal, completion or prior-proof drift,
forged self-consistent stamps, wrong receipt mode or link count, duplicate or
out-of-order receipts, fixed invocation identity, concurrent directory-lock
contention, partial receipt writes, directory replacement, exact rollback, a
third proof, terminal-fact substitution, and unscoped AAPL substitution.

## Disclosed initial completion-envelope stop

The first attempted canonical Stage 12D proof stopped during Stage 12C
completion-receipt digest validation. It stopped before the immutable SQLite
connection, fixed database queries, or a success receipt. The only durable
Stage 12D artifact at that point was the empty private receipt directory; there
was no proof file or partial proof file.

The Stage 12C completion JSON has an envelope containing contract and sha256.
Its embedded and pinned sha256 authenticates the business material while
excluding both envelope fields. The initial Stage 12D validator correctly
required the strict envelope shape and contract, but mistakenly included
contract when recomputing the embedded digest.

The correction preserved the strict envelope and contract checks while
recomputing business material as every field except contract and sha256.
Both synthetic completion producers were aligned with the actual producer:
construct the business material, hash it, then add contract and sha256 to the
envelope. A focused regression proved that the embedded business-material hash
passes, while the full serialized-file SHA-256 and the obsolete
contract-inclusive hash are rejected.

This correction did not alter the Stage 12C receipt, the database, the scope,
or any canonical fact. The stopped invocation issued no provider request and
performed no database operation. The later successful ordinal-1 proof was
therefore a fresh proof invocation after correcting local validation, not a
retry of a data operation.

## Canonical proof receipts

The private evidence root is:

    data/.stage12/market-v1/stage12d-no-transfer-adoption/

It is mode 0700. Exactly two mode-0600 receipt files exist:

| Ordinal | Immutable filename | Completed at (UTC) | Receipt SHA-256 |
| --- | --- | --- | --- |
| 1 | proof-1-892848057d47434db503f604e8999833.json | 2026-08-17T02:57:55.455231Z | 243f6fb3c6a3f833bf148861025a5257a36cbbed54e6b86971624eaac44a0df8 |
| 2 | proof-2-af2ac00f5bd64cbca7d4db1c1a3eac60.json | 2026-08-17T03:02:37.894229Z | 1fbcc3a278b824b158abd452a6085c52fed2b48bf2d82c6ac32c4286d7b85066 |

The 32-lowercase-hex identity in each filename exactly equals its receipt
invocation identity. The identities, completion timestamps, and receipt hashes
are distinct. Both receipts bind the same semantic proof SHA-256:

    f504114fae96e8ce0eb1e8e74215f5e0f7ff0d3f1964c615367e2a92a0e94b67

Both also bind the same target identity SHA-256:

    5a7693962ccf3cc2ce5197bd2e4839b35f5a9c9cbf525d0bb96b4c2d4c65eed4

No third receipt, pointer, promotion marker, backup, or partial proof file
exists. Proof 2 securely revalidated proof 1 and required a distinct invocation
identity and receipt hash with the identical semantic proof.

## Read-only access proof

Each canonical invocation:

1. securely opened the Stage 12C completion receipt and retained its no-follow
   descriptor binding through success;
2. securely opened and pinned the direct, single-link canonical target with
   O_RDONLY, O_CLOEXEC, and O_NOFOLLOW;
3. required the WAL and rollback journal to be absent or zero-byte;
4. opened SQLite only through the pinned descriptor URI:

       file:/proc/self/fd/<fd>?mode=ro&immutable=1

5. immediately enabled and verified query_only=1;
6. ran only the fixed proof queries; and
7. rechecked completion authority, any prior receipt, target identity, and all
   main/WAL/SHM/journal stamps before and after the proof and receipt.

There was no ordinary SQLite reader, writable connection, write transaction,
checkpoint, journal-mode change, backup API, caller path, or caller SQL.

## Exact fixed-query evidence

Both receipts record the same complete check object:

| Check | Exact result |
| --- | ---: |
| integrity | ok |
| foreign_key_violations | 0 |
| current_rows | 4,237,131 |
| version_rows | 4,237,873 |
| captures | 5,210 |
| stage12c_captures | 619 |
| stage12c_version_rows | 1,238 |
| stage12c_current_rows | 1,238 |
| aapl_current_rows | 2 |
| duplicate_captures | 0 |
| duplicate_versions | 0 |
| duplicate_current | 0 |
| pointer_anomalies | 0 |
| stage12c_availability_anomalies | 0 |
| stage12c_correction_anomalies | 0 |
| terminal_database_facts | 0 |

Coverage was identity-based rather than aggregate-only. The proof source-loaded
the frozen 629-symbol roster and required the exact 619 published identities.
Each published symbol had exactly one Stage 12C capture and exactly one version
and current row on each fixed date, 2026-08-13 and 2026-08-14, all scoped by the
Stage 12C semantic digest. AAPL was checked through current to version to
capture under that same digest and had exactly the two fixed dates.

The exact ten receipt-sealed terminal identities were:

| Ordinal | Symbol | Outcome |
| ---: | --- | --- |
| 177 | EA | successful empty |
| 201 | EUV | successful empty |
| 302 | IRBO | successful empty |
| 615 | ^AXJO | authorized HTTP 402 |
| 617 | ^FCHI | authorized HTTP 402 |
| 619 | ^GDAXI | authorized HTTP 402 |
| 621 | ^GSPTSE | authorized HTTP 402 |
| 624 | ^KS11 | authorized HTTP 402 |
| 626 | ^NDX | authorized HTTP 402 |
| 628 | ^TWII | authorized HTTP 402 |

Each terminal identity had zero Stage 12C-scoped capture, version, and current
facts. The exact 619 published identities plus these exact 10 terminal
identities account for the full frozen 629-symbol roster without substitution.

## Exact stamp neutrality

Both receipts contain identical pre- and post-stamps for every object, and the
second receipt repeats the same frozen values as the first:

| Object | Exists | Device | Inode | Mode | Links | Size | mtime_ns | ctime_ns |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| main database | yes | 2096 | 1224289 | 33188 | 1 | 6,589,505,536 | 1786920456305419358 | 1786920456305419358 |
| WAL | yes | 2096 | 1401425 | 33188 | 1 | 0 | 1786920456313362528 | 1786920456313362528 |
| SHM | yes | 2096 | 1401426 | 33188 | 1 | 32,768 | 1786920950700553222 | 1786920950700553222 |
| rollback journal | no | -- | -- | -- | -- | -- | -- | -- |

The zero-byte WAL satisfied the immutable-reader precondition. The main
database, WAL, SHM, and absent rollback journal were unchanged across both
canonical invocations and receipt creation.

Stage 12C evidence separately discloses that an earlier ordinary mode=ro read
could update an SHM timestamp without a canonical database mutation. Stage 12D
did not repeat that access mode: it used the pinned immutable descriptor URI
and proved exact SHM as well as main/WAL/journal neutrality.

## Receipt durability and sequence

Receipt work was serialized with a nonblocking exclusive flock on the private
directory descriptor. All receipt listing, reads, creation, unlink rollback,
and directory fsync operations were descriptor-relative. Each success used
O_EXCL and mode 0600, then file fsync, directory fsync, secure reread, strict
schema and digest validation, and final authority/stamp rechecks.

Offline evidence proved that lock contention creates no second receipt, a
partial write is removed through the original directory descriptor, a replaced
receipt directory cannot receive a false success, and a third proof is rejected.
The canonical evidence directory contains only the two valid receipts above.

## Verification method and limitation

The canonical proof invocations themselves opened the pinned target through
mode=ro&immutable=1 and executed the fixed integrity, coverage, count, duplicate,
pointer, availability, correction, terminal, and AAPL queries.

The independent post-proof reconciliation deliberately did not reopen SQLite
and did not recompute a full hash of the 6,589,505,536-byte database. It
reconciled the two immutable receipt payloads, receipt self-digests, common
semantic proof, scope and Stage 12C bindings, exact check results, target
identity, and full pre/post stamp neutrality against the already completed
proof queries. Accordingly, this record does not claim a newly computed
post-proof full-database hash or a backup/recovery proof.

That limitation is consistent with the no-transfer objective: Stage 12C had
already established the canonical baseline and facts, while Stage 12D proved
quiet adoption without copying or rewriting the database.

## Disposition and nonclaims

Stage 12D is complete. The existing data/market.sqlite is adopted and frozen as
the project-local canonical market target under registry 2.14.0 and schema
1.8.0.

This result creates no promotion pointer and authorizes no public consumer,
tool, dashboard, export, scheduler, provider, credential, network activity,
backup, restore, recovery exercise, retained-source retirement, migration, or
registry revision. Stage 12E remains closed and requires a separate explicit
user decision.

## Related records

- [ADR 0010: Stage 12D no-transfer market adoption](../adr/0010-stage12d-no-transfer-market-adoption.md)
- [ADR 0009: canonical market operational path](../adr/0009-canonical-market-operational-path.md)
- [Stage 12D project-local operationalization contract](STAGE12D_PROJECT_LOCAL_OPERATIONALIZATION.md)
- [Stage 12D machine-readable scope](../../config/stage12d_market_no_transfer_adoption_v1_scope.json)
- [Stage 12C evidence](STAGE12C_EVIDENCE.md)
- [Stage 12C market gap contract](STAGE12C_MARKET_GAP_V1.md)
- [Stage 12B evidence](STAGE12B_EVIDENCE.md)
