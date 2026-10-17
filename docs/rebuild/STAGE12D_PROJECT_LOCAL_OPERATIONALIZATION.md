# Stage 12D -- Project-Local Market Operationalization Without Transfer

**Status:** Completed and independently verified under the immutable
[Stage 12D evidence record](STAGE12D_EVIDENCE.md).

**Decision date:** 2026-08-16
**Contract:** quant_data.stage12d_market_no_transfer_adoption_v1
**Version:** 1.0.0

The exact canonical receipts are
`243f6fb3c6a3f833bf148861025a5257a36cbbed54e6b86971624eaac44a0df8`
and
`1fbcc3a278b824b158abd452a6085c52fed2b48bf2d82c6ac32c4286d7b85066`.
They bind semantic proof
`f504114fae96e8ce0eb1e8e74215f5e0f7ff0d3f1964c615367e2a92a0e94b67`,
semantic scope
`83ff9be19997eb77c9af025b439adf9a977a42113ab8ea413125a1b88f77c981`,
and target identity
`5a7693962ccf3cc2ce5197bd2e4839b35f5a9c9cbf525d0bb96b4c2d4c65eed4`.
The database, WAL, SHM, and journal stamps were neutral across exactly two
proofs, and a third proof is rejected. Independent post-proof reconciliation
did not reopen SQLite or compute a new full-database hash; it makes no backup
or recovery claim.

## Boundary

Stage 12D is the narrow project-local no-transfer adoption/freeze gate after completed Stage 12C. Its only target is:

    data/market.sqlite

It creates no second multi-gigabyte database. It does not transfer, copy, move, replace, promote, back up, restore, compact, retire, or reopen a retained source. It authorizes no migration, registry change, provider request, credential/environment use, network, scheduler, public consumer, tool, dashboard, export, caller path, caller SQL, or general database-read helper. Stage 12E remains closed.

The zero-argument canonical entry point itself binds the approved project root, target, completion receipt, scope, and receipt root. The public runner is fixture-only: it accepts only a strict system-temporary project root with reviewed synthetic authority, and rejects canonical, former-default, retained-source, arbitrary, alias, symlink, and nonempty roots before SQLite access.

## Frozen authority

| Binding | Fixed value |
| --- | --- |
| Target | data/market.sqlite |
| Registry / schema | 2.14.0 / 1.8.0 |
| Stage 12C receipt SHA-256 | 0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88 |
| Stage 12C plan SHA-256 | 5e5c07f03313c1a0d3d3980fa8a900973a301664fecf84e6d37ab3c2f38a92f1 |
| Stage 12C raw scope SHA-256 | 24d8448c124cedb5deb745b50943291d955f05e7a95ac1d9747a7d51b6760226 |
| Stage 12C semantic scope SHA-256 | 2c11fd5bfe99160e7949db3a87f2e3b1616a829b975424df40ec7f4fb16d5392 |
| Ledger | 619 published, 3 successful-empty, 7 authorized HTTP 402, 629 closed |
| Global facts | 4,237,131 current; 4,237,873 versions; 5,210 captures |
| Stage 12C facts | 619 captures; 1,238 versions; 1,238 current rows |
| Evidence root | data/.stage12/market-v1/stage12d-no-transfer-adoption/ |
| Maximum successful proofs | two, serially ordinal 1 then 2 |

The scope loader requires the byte-exact raw manifest SHA-256 and semantic SHA-256. Unknown/missing keys, type/range/policy/path/count/digest drift and forged dataclasses fail closed.

## Guarded authority, target, and SQLite access

Before connecting, the runner holds the Stage 12C completion receipt through a direct O_RDONLY|O_CLOEXEC|O_NOFOLLOW guard. It requires a regular current-user single-link private receipt, strict JSON, exact payload SHA-256, and exact completion/plan/scope/ledger/final-target/final-check/sealed-terminal bindings. It rechecks held-file content and direct pathname identity before connection, during fixed checks, before receipt creation, after receipt creation, and immediately before success. This is a strict completion receipt read, not a provider raw-body or retained-source read.

The database must be a direct regular non-symlink with one link. Its held O_NOFOLLOW guard and direct pathname identity are rechecked throughout. Main, -wal, -shm, and -journal receive pre/post stamps. -wal and -journal must be absent or regular zero-byte files; -shm may be absent or present but must remain unchanged.

A stamp is exactly:

    {"exists": false}

or:

    {
      "ctime_ns": "integer",
      "device": "integer",
      "exists": true,
      "inode": "integer",
      "kind": "regular",
      "link_count": "integer",
      "mode": "integer",
      "mtime_ns": "integer",
      "size": "integer"
    }

The complete receipt retains full physical pre/post stamps for main, wal, shm, and journal. Validation requires each pre/post pair to agree and each recorded stamp to exactly equal the frozen current stamp. Any path replacement, link change, sidecar creation, nonzero WAL/journal, or stamp difference is a systemic failure.

SQLite is reached only through:

    file:/proc/self/fd/<held-target-fd>?mode=ro&immutable=1

with uri=True, followed immediately by PRAGMA query_only=ON and an exact query_only=1 check. No normal reader, writable connection, write lock, checkpoint, journal_mode, backup, restore, helper-selected path, or caller SQL is permitted. The zero-WAL/journal precondition is required for immutable safety; it also avoids the known plain mode=ro -shm timestamp side effect.

## Exact source-derived checks

For a canonical proof, Stage 12D source-loads and revalidates the frozen Stage 12A, 12B, and 12C manifests without opening another store. The exact 629-symbol roster minus receipt-sealed terminal symbols gives the exact 619-symbol published map. For every published symbol, facts where:

    capture.scope_manifest_sha256 == completion.scope_semantic_sha256

must have exactly one capture and exactly the two dates 2026-08-13 and 2026-08-14, with one version and one current row for each date. No substitute symbol or date is accepted. Every receipt-sealed terminal symbol must have zero scoped capture, version, and current facts. AAPL is checked through current -> version -> capture under the same digest with exactly those two dates; unscoped baseline AAPL rows do not qualify.

The fixed checks also require integrity_check == "ok"; zero foreign-key violations, duplicate capture/version/current results, current-pointer anomalies, Stage 12C availability anomalies, Stage 12C correction anomalies, and terminal database facts; and the frozen aggregate counts.

## Private receipts, locking, and rollback

The evidence directory is mode 0700 and locked through a directory descriptor with flock(LOCK_EX|LOCK_NB). All listing, read, write, unlink, and fsync actions are descriptor-relative. The pathname identity is compared with the locked descriptor immediately before and after receipt work, so directory rename/replacement fails closed.

There is exactly one valid receipt per ordinal and no unexpected proof file. A success filename is exactly:

    proof-{ordinal}-{32 lowercase hex UUID}.json

Ordinal is 1 or 2. The 32 lowercase hexadecimal UUID in the filename must equal the JSON invocation.identity. Proof 2 holds and rechecks proof 1 via a no-follow directory-fd guard. The invocation UUID and immutable receipt SHA-256 must differ from proof 1; the semantic proof SHA-256 must match. A third proof is ConflictError.

Creation is a single O_EXCL|O_NOFOLLOW|O_CLOEXEC, mode-0600 file, followed by file fsync and directory fsync. It is strictly reread and validated before success. On any write, fsync, reread, validation, authority, stamp, or directory-identity failure, the runner securely removes exactly the new inode through the original locked directory descriptor and fsyncs that directory. No overwrite, rename, pointer, promotion, or partial success receipt is allowed.

## Exact strict receipt structure

The schema is quant_data.stage12d_market_no_transfer_receipt_v1. The top-level object has exactly these keys:

    checks
    contract
    invocation
    receipt_schema
    scope
    semantic_proof_sha256
    sha256
    sidecars
    stage12c_binding
    target
    version

sha256 is canonical JSON SHA-256 over every other top-level field. The exact nested fields are:

    version: "1.0.0"
    contract: "quant_data.stage12d_market_no_transfer_adoption_v1"
    receipt_schema: "quant_data.stage12d_market_no_transfer_receipt_v1"

    invocation:
      completed_at
      identity
      ordinal

    scope:
      manifest_sha256
      registry_revision
      schema_version
      source_file_sha256

    stage12c_binding:
      authorized_http_402
      closed
      completion_receipt_sha256
      plan_sha256
      published_complete
      scope_semantic_sha256
      successful_empty

    target:
      post_stamp
      pre_stamp
      project_relative_path

    sidecars:
      journal: {post_stamp, pre_stamp}
      shm: {post_stamp, pre_stamp}
      wal: {post_stamp, pre_stamp}

The flat checks object has exactly these keys and canonical values:

    aapl_current_rows: 2
    captures: 5210
    current_rows: 4237131
    duplicate_captures: 0
    duplicate_current: 0
    duplicate_versions: 0
    foreign_key_violations: 0
    integrity: "ok"
    pointer_anomalies: 0
    stage12c_availability_anomalies: 0
    stage12c_captures: 619
    stage12c_correction_anomalies: 0
    stage12c_current_rows: 1238
    stage12c_version_rows: 1238
    terminal_database_facts: 0
    version_rows: 4237873

Fixture receipts use the same fixed schema and algorithm with reviewed synthetic expected values.

## Semantic proof identity

semantic_proof_sha256 covers only stable logical authority: access policy, flat checks, contract/version, scope manifest/source/registry/schema bindings, and completion authority. It excludes all paths; device, inode, mode, size, mtime, and ctime values; invocation identity, ordinal, time, filename, and receipt SHA-256; secrets; and provider/raw bodies.

Canonical semantic completion authority includes the frozen Stage 12C completion receipt SHA-256 plus plan, scope, and flat ledger fields. Fixture semantic completion authority includes a stable validated fixture_id and the same logical plan/scope/ledger fields, but excludes actual root-specific synthetic completion SHA-256. The full receipt always retains and validates its actual completion receipt SHA-256, target identity, and full physical stamps. Thus distinct valid receipts and fixture roots can share a semantic digest without weakening canonical authority.

## Offline gate, exit, and nonclaims

All tests use explicit synthetic temporary roots and never open the canonical target, retained source, credentials, provider, network, scheduler, or backup path. Coverage includes two successful proofs, third-proof rejection, cross-root semantic equality with distinct completion SHA/identity, nonzero WAL/journal, symlink/hardlink/alias/replacement, guard drift, forged authority/count/digest/stamp data, partial-write rollback, UUID collision, exact permissions, and stamp neutrality.

A deterministic regression holds the receipt-directory flock after a valid proof 1, then starts proof 2 after lock acquisition is known through synchronization events rather than sleeps. The concurrent attempt must return exactly ConflictError, create no proof 2, and leave target/sidecar stamps unchanged. After release, a normal proof 2 may succeed with the same neutrality.

Stage 12D is complete: the frozen-scope, fixture, bounded repository, and
independent gates passed, and exactly two canonical read-only proofs produced
immutable receipts with exact target/sidecar neutrality. The fixed checks
record 4,237,131 current rows, 4,237,873 versions, 5,210 captures, 1,238
Stage 12C-scoped current/version rows, and 619 Stage 12C captures, with every
reviewed anomaly count at zero. It proves only the reviewed adoption/freeze
condition, not a transfer, promotion, backup validation, public release, or
scheduler authorization. Stage 12E remains closed and requires separate
explicit user authorization.

## Related records

- [Stage 12D evidence record](STAGE12D_EVIDENCE.md)
- [ADR 0010: Stage 12D no-transfer market adoption](../adr/0010-stage12d-no-transfer-market-adoption.md)
- [ADR 0009: canonical market operational path](../adr/0009-canonical-market-operational-path.md)
- [Stage 12C evidence record](STAGE12C_EVIDENCE.md)
- [Stage 12C two-session Market v1 gap contract](STAGE12C_MARKET_GAP_V1.md)
- [Stage 12B evidence record](STAGE12B_EVIDENCE.md)
- [Scheduling and physical-store locking](SCHEDULING_AND_LOCKING.md)
- [Data and time contracts](DATA_AND_TIME_CONTRACTS.md)
