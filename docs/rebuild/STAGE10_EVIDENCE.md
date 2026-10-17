# Stage 10 Evidence Record

Status: **The retained base and historical-extension populations are complete,
internally coherent, and independently re-inspected. Both remain private,
non-production candidates.**

## 1. Purpose and boundary

This record documents the existing Stage 10 FMP market-history artifacts under
the exact target:

`/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1`

It records two immutable results:

1. the original full-history population for the frozen 629-symbol roster; and
2. the additive eight-window historical extension for that same roster.

The provider requests are complete and must not be repeated. This record does
not authorize scheduling, promotion, retirement, public exposure, tools,
dashboards, Atlas, exports, destructive cleanup, Russell 2000 constituents,
`^RUT`, or `IWM`.

## 2. Base population

The base candidate was generated at `2026-08-12T19:36:18.669433Z` with state
`private_candidate_only_no_operational_promotion`.

| Evidence | Retained value |
| --- | --- |
| Scope | 629 frozen symbols |
| Successful symbols | 621 |
| Retained failed symbols | 8 |
| Current price rows | 764,512 |
| Immutable price versions | 764,512 |
| Registry projection | schema `1.6.0`, registry `2.8.0` |
| Registry source SHA-256 | `c64aceefcc9a37cc0669398ea4d5817d997a5d82167941a204c2b77fffce77f9` |
| Scope manifest SHA-256 | `0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd` |
| Roster SHA-256 | `0cf96563efa8667efbf41b03e85d74970cc5d8e789936e98fdb67bca82347298` |
| Migration | `market:0010_stage10_market_history` |
| Migration SHA-256 | `a7703655f6fe8089431eacdc78600582d6f5582589c0c38b531a2b93c7dc041a` |

The eight retained base failures are `EUV`, `^AXJO`, `^FCHI`, `^GDAXI`,
`^GSPTSE`, `^KS11`, `^NDX`, and `^TWII`. The failure-manifest SHA-256
is `1b0b2a64ca302b1c5c411f927d625e852fd176a0d0aeebba20ba71ba9b9ebceb`.

The base reconciliation records raw-capture reparsing, canonical/raw equality,
correction lineage, unchanged source reads, and source/restored equality.

### Base receipt pins

| Target-relative artifact | File SHA-256 | Receipt-chain value |
| --- | --- | --- |
| `private/resume.json` | `1c0a9f941d829170e4085ef133df6343dadf32d975168ce43aa3724749553795` | phase state |
| `private/completion.json` | `14fe633931c313b38190a8828823a0a7aa87d9a471ee99d4cfc7039e9bbb4c20` | completion `dfbaade3f95dc63d87d619fe923cb993da248bf0c1151a193de9f2d91422e135` |
| `private/candidate/promotion-candidates/stage10-fmp-market-history-v1-0782e0fdf39c3113.json` | `d2ec4c1a6969e8bc0986bb78434263a02e6d7bc0988c3819cd4d9d1022fe2178` | receipt `cd7bc0aa6c61b020e95a02ec19faa814c69cb8650c72bf443fb6d4be4dd44372`; evidence `84c898738bf0d849e394ca157eb3013f08f6f134b60d196ed7aceafa1105422b` |

The file hashes and receipt-declared hashes are intentionally labeled
separately; they are not interchangeable.

## 3. Historical extension

The successor candidate was generated at `2026-08-13T16:45:48.055678Z`.
Its state remains `private_candidate_only_no_operational_promotion`.

The frozen plan contains exactly 629 symbols times eight inclusive,
non-overlapping windows:

| Outcome | Count |
| --- | ---: |
| Planned windows | 5,032 |
| Complete windows | 3,970 |
| Successful empty windows | 1,004 |
| Authorized terminal outcomes | 58 |

The arithmetic closes exactly: `3,970 + 1,004 + 58 = 5,032`.

The 58 terminal outcomes cover nine tickers:

- 56 sealed HTTP 402 `operator_authorized_entitlement_unavailable` outcomes:
  all eight windows for `^AXJO`, `^FCHI`, `^GDAXI`, `^GSPTSE`,
  `^KS11`, `^NDX`, and `^TWII`; and
- two HTTP 200 `operator_authorized_known_listed_empty` outcomes for the
  `2025-01-01` through `2026-08-12` window: `EUV` and `IRBO`.

The final reconciliation records 3,970 extension captures, 4,235,893 current
rows, and 4,236,635 immutable versions. Raw-sidecar reparsing,
canonical/raw equality, correction lineage, unchanged reads, and
source/restored equality are all retained as true.

### Extension receipt pins

| Target-relative artifact | File SHA-256 | Receipt-chain value |
| --- | --- | --- |
| `private/candidate/stage10-history-extension-v1/live/manifest.json` | `59e5100ed1007c40b47e8d52ccb12922feeca833e163a60b62c373370e3915fd` | manifest `472407a3085094cc9bdb028fa672556a13ce51c10299240acf68c875bf267a65`; plan `364ae14f62e9a43621f6f2153dd9194780f403b142dbdfd349637a9dd4952a2d` |
| `private/candidate/stage10-history-extension-v1/live/resume.json` | `dd00bb333f4ec2f6cfcc4df4253bb740f0e31e3eadb8048e70e4ad59517109d7` | complete continuation ledger |
| `private/candidate/stage10-history-extension-v1/live/completion.json` | `e88416a61d481f4e867058b8da07af13017b971535e28bed261a7d9dfe63d0d9` | completion `b7068b208616fe66bcb75c9d2324a1a999dd60f93c1174b59e940f4cd9c9e7fd` |
| `private/candidate/stage10-history-extension-v1/live/candidate/candidate-receipts/stage10-history-extension-v1-364ae14f62e9a436.json` | `c00b10ac490b219a40c17d70c7439bfe8a994ad51ac49eab835e401de19d2c3f` | receipt `2cd6c2204fbdc334f3cdc62acb1b799e3d1f79ff5ccb6147d17f083e8c2d42e6`; evidence `3a47a9e0365aa620bd46d6e49ca2bfa580646f6ca5f436ece491a20b590ef70e` |

The extension cohort is pinned to canonical schema `1.7.0`, registry
`2.9.0`, and source SHA-256
`7e8ec6fc38d5a24962460d9c78a4df7754d3b29ad4b74c0c5e8ae807cd59ed56`.

## 4. Independent read-only inspection

The retained artifacts were inspected without provider access. Immutable
read-only SQLite checks matched the receipts:

- current rows: 4,235,893;
- immutable versions: 4,236,635;
- total base-plus-extension captures: 4,591; and
- missing current-version references: 0.

The dependency-free working-tree suite also passed before this record was
written:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
Ran 509 tests in 863.807s
OK
```

No credential, provider request, scheduler action, promotion, or destructive
operation was used during the inspection.

## 5. Evidence qualification and disposition

The retained receipts prove the population results that exist now. The
accepted repository did not contain a point-in-time Stage 10 evidence record
showing that independent verification preceded the live calls. That historical
sequencing cannot be reconstructed retroactively and is not asserted here.

This is an evidence-governance limitation, not a detected data-integrity
failure. The retained base and extension populations are treated as complete,
immutable candidate evidence and must not be re-requested. Promotion,
retirement, scheduling, and every public or destructive action remain closed.
