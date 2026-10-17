# Stage 7 Acceptance Evidence

Status: Bounded offline manual fixture rehearsal independently verified
Gate date: 2026-08-11
Scope: bounded manual fixture job rehearsal only

## 1. Boundary

Stage 7 adds a disabled, manual-first operations layer over the accepted Stage
6 synthetic four-store cohort. Canonical registry revision `2.5.0`, schema
`1.3.0`, declares exactly eight ordered `manual_fixture_only` jobs:

- `news-hourly`;
- `sec-daily`;
- `options-close`;
- `macro-daily`;
- `market-close`;
- `expectations`;
- `company-weekly`; and
- `macro-monthly`.

All eight have `scheduling_enabled: false`. The registry declares zero
exports. No live provider, runtime network, scheduler installation/start/
update/removal, promotion, hosting, Atlas, Stage 8, default path, credential,
or destructive operation is authorized or implemented.

The frozen Stage 6 `2.4.0`/`1.2.0` projection remains jobs-empty and
preserves its approved evidence. Earlier Stage 1 through Stage 5 projections
also remain unchanged.

## 2. Frozen implementation and evidence

- [Canonical registry](../../config/system_registry.json) and
  [strict registry loader/profiles](../../quant_data/registry.py).
- [Physical store locks](../../quant_data/stores.py) and
  [pre-acquired ingestion session](../../quant_data/ingestion.py).
- [Fixture step executor](../../quant_data/operations/fixture_executor.py),
  [job runner](../../quant_data/operations/job_runner.py),
  [retry policy](../../quant_data/operations/job_retry.py), and
  [private receipts/logs](../../quant_data/operations/job_receipts.py).
- [Restricted manual wrapper](../../quant_data/operations/manual_job.py).
- [Deterministic Stage 7 harness](../../quant_data/stage7.py),
  [integration tests](../../tests/test_stage7_integration.py), and
  [approved golden](../../tests/fixtures/stage7_golden.json).
- Focused operation tests:
  [runner](../../tests/operations/test_job_runner.py),
  [receipts](../../tests/operations/test_job_receipts.py),
  [retry](../../tests/operations/test_job_retry.py),
  [fixture publication](../../tests/operations/test_stage7_fixture_publication.py),
  [fixture executor](../../tests/operations/test_stage7_fixture_executor.py),
  and [manual wrapper](../../tests/operations/test_manual_job.py).

The approved deterministic pins are:

- Stage 7 evidence:
  `63a0179e1149b73afaa50aff586160c667262dd834fd38f0f0d5043c926fd1f7`;
- Stage 6 evidence:
  `868b84691629de835c05219355e0741e3691cd6e34f502abe59e0790d2573794`;
- job catalog:
  `40a8e1d7e0d3136e447d5d98a455ea00a11234a1267ae8113c8102890c162a55`;
- dry-run catalog:
  `6ecbb10b7c83fcaf72ec3dbf31785d3583104ba775dc1f8fc08984cda99cae95`;
- mocked outcome matrix:
  `3bbce1de4bd6ec4224abe5caedd796357f3c262bb11f8121c1e81bc886cc750f`;
- receipt manifest:
  `f37ccf5335f263193252555b13e94c137cb14c97b85bfff69b5bda3f6acc5684`;
- real fixture replay:
  `fbb33b7ec6317a37b4804918d63a10eaa31e893b9a04400ca6945013038e3f7e`;
  and
- backup manifest:
  `f8dd0b31af6e18a39cb66065effd3c3a8cf0059c1bda3fdc55a8114670d26670`.

The eight dry-run plan pins are:

| Job | SHA-256 |
| --- | --- |
| `news-hourly` | `87dd4ae559ac259128853e104ddfb041becc58231a5066ff62113c439b310d05` |
| `sec-daily` | `c529e994494a22c2e4949894eb44a5f35ed672ec5686a94c57c42bf859a22440` |
| `options-close` | `c436d1d861798154c0bca86653e54a98b93ece9a5cd4cdf1421ae88d09b56de6` |
| `macro-daily` | `7856368b34bfa1175e776860b49c262522d72ef6a9d8aca1ebc5d440dbfbddb4` |
| `market-close` | `5ffa526c25cfa909d9255c3b526a079c6a4f5fbb88c37fc312b2b252da44d720` |
| `expectations` | `f54ab61ccae1616cbd7079ee9707dfd82abc006f61f1ecc31fe2d0a2d95bab27` |
| `company-weekly` | `b44629d34909d15cb05f5213043188e2d77f0676ba5ea56e8ce721322a5fc09f` |
| `macro-monthly` | `5a3b102a066a06e561303a9f7ceb56c833444831b33a9df11b16de23d912faba` |

Migration heads remain Stage 4's reviewed heads: market `0008`, macro
`0011`, company `0007`, and news `0004`; Stage 7 allocates no migration.

## 3. Reproduction and primary result

From the canonical WSL project root:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m unittest   discover -s tests -t . -v
```

Primary result: `Ran 277 tests in 184.733s - OK`.

The deterministic two-root Stage 7 command is:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data   --stage stage7   --project-root /home/volatility/Python_Projects/Quant_Data_Infra   --store-root <empty-work-root-1>   --second-store-root <empty-work-root-2>
```

It emitted exactly one strict-JSON line, exited zero, matched the Stage 7 and
Stage 6 evidence pins above, and reported eight jobs, zero enabled schedules,
zero live calls, zero scheduler installations/starts, and zero exports. A
second invocation against a nonempty root failed closed without replacing its
contents.

A pure wrapper plan is reproduced with:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m   quant_data.operations.manual_job   --project-root /home/volatility/Python_Projects/Quant_Data_Infra   --store-root <explicit-temporary-store-root>   --state-root <explicit-temporary-private-state-root>   --job market-close   --dry-run
```

This dry run does not open or initialize stores, acquire a lock, call a
provider, write state, or mutate a scheduler. The non-dry wrapper is tested
only against explicitly initialized synthetic stores; it is not a live-ready
command.

## 4. Primary-gate checks

The primary gate proves:

- strict job declarations reject unknown fields, dependency cycles, invalid
  calendars, unbounded retry, wrong derived store sets, enabled schedules, and
  export references before side effects;
- every fixture candidate is parsed and sealed before locks;
- the complete path-derived lock set is sorted, acquired under one deadline,
  and released in reverse; publication reuses the held capability;
- no provider, retry sleep, or parsing occurs under a write lock or
  transaction;
- changed publication is append-only and exact replay is a total no-write;
- partial/error outcomes never create tombstones or success markers;
- active prepare/publication timeouts produce `124` and release locks;
- lock, configuration, I/O, dependency, retry, and partial outcomes use the
  documented deterministic aggregate exit policy;
- private log, optional monthly marker, and receipt publication is bounded,
  immutable, path/secret-free, atomic, and receipt-last;
- receipt I/O failure cannot report success;
- the manual wrapper returns its receipt aggregate code unchanged;
- a real `market-close` fixture run changes once, then replays unchanged with
  an identical all-store fingerprint after the first run;
- backup/restore uses SQLite backup, preserves source fingerprints, passes
  integrity and foreign-key checks, and matches read-only source state; and
- two independent roots produce identical path-free evidence.

Focused results were `43/43` operations tests and `3/3` Stage 7 integration
checks. Generated registry artifacts were byte-current.

## 5. Independent verification and gate state

Independent SolUltra verification **passed** on 2026-08-11 with no findings. It
reran the exact full suite (277/277 in 170.731s), the operations suite (43/43),
the Stage 7 registry and integration checks (6/6), and the generated-artifact
freshness check. It independently reproduced the path-free two-root CLI evidence
and approved pins, exact manual-wrapper process exit codes 64, 74, 75, 78, and
124, changed-then-unchanged replay, complete-set locking and release behavior,
receipt permissions and publication ordering, and WAL-safe backup/restore
equality. Its final workspace seal was identical to its initial seal. The
bounded Stage 7 exit gate is closed. At this Stage 7 decision, Stage 8 remained
closed pending separate authorization; its later disposition is recorded in
[Stage 8 evidence](STAGE8_EVIDENCE.md).

External task definitions, exact recovered calendars, timezone/DST behavior,
operating identity, scheduler installation/update/removal/start, live
providers, exports, promotion, hosting, Atlas, and destructive operations all
require separate future authorization. This primary evidence makes no claim
that any of those paths are ready.
