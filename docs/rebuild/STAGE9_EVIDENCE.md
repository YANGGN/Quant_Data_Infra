# Stage 9 Evidence Record

Status: **Bounded live FMP population and candidate receipt independently verified; operational promotion and retirement remain excluded.**
Scope: one manual FMP daily OHLCV backfill for `SPY`, inclusive `2026-07-01` through `2026-07-31`, into an exact non-production root.

## 1. Authorization boundary

| Item | Fixed contract |
| --- | --- |
| Collector | `fmp.market.daily_price_backfill` |
| Provider/data family | Financial Modeling Prep (FMP) daily end-of-day OHLCV |
| Request scope | `SPY`; inclusive calendar dates `2026-07-01` through `2026-07-31`; exact expected NYSE coverage is 22 trading dates |
| Transport | One request only, no retry; bounded to 22 rows, 262144 bytes, and 30 seconds |
| Credential | Environment-variable name `FMP_API_KEY` only; its value is never stored, logged, included in a semantic identity, or placed in a command line |
| Target | `/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607` only |
| Durable outcome | Reconciliation plus a private candidate-only receipt; no operational promotion and no old-store retirement |

The scope is additive and market-only. It does not authorize another symbol, date range, endpoint, provider, retry, scheduler, default path, public tool, dashboard relation, Atlas projection, host/deployment action, export, or destructive cleanup.

## 2. Registry and migration pins

- Canonical registry: revision `2.7.0`, schema `1.5.0`.
- Additive migration: `market:0009_fmp_daily_price_backfill`.
- Reviewed migration resource: `quant_data/migrations/market/0009_fmp_daily_price_backfill.sql`.
- Migration SHA-256: `f2e664888449cb3d1885f1199d1a22996709cb87f4f3ac005bd8f91c0cc62ba0`.
- Canonical registry source SHA-256: `46ff0f92f92380c203aacaf54e511ed219f3bc43edaaba0fb5a6fbe29b95a145`.
- Registered datasets: `market.fmp.daily_price_evidence`, `market.fmp.instruments`, and `market.fmp.daily_prices`.
- Isolated physical relations: `fmp_daily_price_captures`, `fmp_instrument_identities`, `fmp_daily_price_versions`, and `fmp_daily_prices`.

The FMP datasets have no public-tool, dashboard, or Atlas export exposure. Raw provider response bytes are private evidence, not a public artifact.

## 3. Primary deterministic offline gate

The network-blocked two-root harness uses an FMP-shaped synthetic response with the exact request scope. It verifies changed publication, unchanged replay with zero persistent writes, coverage/provenance reconciliation, SQLite online backup and clean restoration, a correction applied only to a scratch restoration, and a private candidate-only receipt.

Current path-free offline evidence SHA-256:

```text
1851168a239c2b3e344db77d20439a1e1a8264d0eae65c950e33f4dd06c583c4
```

Current reconciliation/repopulation evidence SHA-256:

```text
5bcc8dc077a0e5df3a502eef9eb6d46df7346eae1211d5b56ccf925f5a563f42
```

The candidate receipt digest is
`d23c9035570e5240dfcb05a7c94a40183370c9848a5fc5326e0ac7ae02944f3d`;
its private file digest is
`3ab7d69158822138c5824192e4695a512e4fad0cdddaee5eef7854a230e2a9b3`.
The source/backup/restored logical digest is
`12c95f3a8dc16475f5f73126778f285184d5cd741b9962daf21f3794fa1c249c`.

Primary validation completed on 2026-08-11:

- the corrected focused Stage 9 hostile and Stage 8 compatibility set passed
  30/30 in 97.740 seconds;
- the corrected full dependency-free offline suite passed 344/344 in 387.262
  seconds;
- generated-artifact freshness and `git diff --check` passed;
- a fresh separate-process two-root CLI run returned zero with one canonical
  JSON stdout line and empty stderr, while a nonempty retry returned 2 with one
  `conflict` stderr line and left both roots byte/mode unchanged; and
- with `FMP_API_KEY` forcibly absent, the real manual wrapper returned 78 with
  one safe `invalid_configuration` stderr line, empty stdout, no target
  creation, and no network call.

These are primary offline results. They are not a claim that provider preflight
or a live FMP request has passed.

An independent review finding required the canonical Stage 9 migration to be
recorded in the reviewed [migration reconstruction map](MIGRATION_RECONSTRUCTION.md).
After that correction, independent SolUltra re-verification completed with no
remaining findings:

- the actual default scratch-correction callback passed end-to-end offline in
  3.217 seconds with one synthetic provider call and zero network attempts;
- the focused Stage 9 and Stage 8 compatibility gate passed 30/30 in 96.881
  seconds;
- the full dependency-free suite passed 344/344 in 372.270 seconds;
- the fresh two-root Stage 9 CLI passed in 37.662 seconds with evidence SHA
  `1851168a239c2b3e344db77d20439a1e1a8264d0eae65c950e33f4dd06c583c4`,
  and nonempty retry was byte/mode neutral; and
- the real missing-key wrapper returned 78 with sanitized output and left the
  approved target absent.

No live request or credential value was accessed during independent verification.

## 4. Verified live population

On 2026-08-12 the approved wrapper made exactly one authenticated FMP request
for the fixed `SPY` scope and wrote only the exact non-production target. The
credential was read from an ignored local dotenv file after its mode was
tightened to `0600`; its value did not appear in output, stores, receipts, or
the project tree.

The wrapper returned `succeeded`, exact replay returned `unchanged`, the
scratch-only correction rehearsal passed, and the durable state remained
`candidate_only_no_operational_promotion`.

Independent read-only verification found no correctness or safety issues:

- all 22 expected dates from `2026-07-01` through `2026-07-31` were present;
- the source contains one 5,067-byte provider capture, 22 immutable versions,
  and 22 current rows, and the stored response matches every canonical OHLCV row;
- response SHA-256 is
  `c7535156322362a5c47c24c284bc9192fa0e310a85246f97944deb6ff573c7c2`
  and semantic identity is
  `77bc61ff7dc51fb2cd028290e335bb387969c17f71948d6de42ea723c2796e88`;
- source, backup, and restored logical fingerprints match
  `b53fe88714463e978ff2abd09036d66bd33bd68ccd4fdca213ef094c8077c33d`;
- the isolated rehearsal contains two captures, 23 versions, maximum
  correction sequence 2, and exactly one changed date;
- receipt SHA-256 is
  `044d7802b91585b1f14897d40cf147f1e9a48663c95a0f47d7046edd3e8fac56`,
  evidence SHA-256 is
  `34bc5a557e531313f3ac9e2842c8ea898d5382e37d994a0e4fd5226be5558dba`,
  and receipt-file SHA-256 is
  `d6d9eae2b362b984b056cf3c261956cda3112c74a148b196682c027e2a0757ae`; and
- all 16 SQLite databases passed integrity, foreign-key, query-only, and exact
  registry migration-ledger checks. The target and repository reseals matched.

No provider request was repeated during verification. Provider licensing,
retention, display, and redistribution rights remain the operator's
account-specific responsibility and were not independently assessed.

## 5. Provider provenance and use prerequisite

The adapter targets FMP's documented [full historical end-of-day price endpoint](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-full). The implementation deliberately uses process-header authentication rather than a credential-bearing URL; FMP's [stable API documentation](https://site.financialmodelingprep.com/developer/docs/stable) documents header authorization.

Before any live request, the operator must confirm that the current FMP account and plan permit this endpoint, request/data usage, storage/retention, display, and any redistribution contemplated by the actual use. FMP controls those terms and may change them; consult its current [Terms of Service](https://site.financialmodelingprep.com/developer/docs/terms-of-service) and account-specific agreement. This repository does not reproduce provider terms or represent a licensing opinion.

The bounded response above is recorded only as private non-production evidence.

## 6. Credential and evidence handling

- Configure `FMP_API_KEY` securely in the invoking WSL process; never paste it into chat, the registry, a fixture, source control, a command line, or a receipt.
- Offline tests use injected transports and must not require a credential or network access.
- The live wrapper must preflight scope, credential presence, registry/migration state, and exact target containment before creating a target directory or opening a network connection.
- A failed authentication, schema, transport, rate-limit, or target-preflight attempt is not an unchanged result and must not create canonical data.

## 7. Gate disposition

| Gate | Current status |
| --- | --- |
| Deterministic two-root harness | Primary gate passed; evidence recorded above |
| Full dependency-free suite, Stage 9 CLI, generator check, and wrapper failure checks | Primary gate passed |
| Independent SolUltra verification | Passed after correction; no findings |
| Credential and exact-target preflight | Passed; ignored dotenv mode `0600`, exact target only, credential value not persisted |
| One live FMP receipt with 22-date reconciliation, replay, backup/restore, scratch correction, and candidate receipt | Passed; independently verified with no findings |
| Operational promotion or old-store retirement | Explicitly excluded from this stage |

The later 2026-08-14 Stage 8 browser-automation waiver is independent of Stage
9 and does not broaden the Stage 9 scope.
