# Stage 12A Evidence Record

Status: **Implemented — bounded offline authority/path gate independently verified. Stage 12A did not itself authorize Stage 12B; Stage 12B was later authorized and completed offline and fixture-only. Stages 12C through 12E remain closed.**

Verification date: 2026-08-15

## 1. Purpose and authorized boundary

Stage 12A freezes the Market v1 authority, retained Stage 10 coverage claims,
canonical project-relative market path, historical registry compatibility, and
ordered successor gates.

The canonical market default is `data/market.sqlite`, resolving from the
explicit project root to
`/home/volatility/Python_Projects/Quant_Data_Infra/data/market.sqlite`.
That resolved absolute path is explanatory only: the gate did not create, open,
move, copy, populate, validate, promote, or otherwise operate on the real
default store. It also did not inspect, reopen, move, or populate the retained
Stage 10 candidate.

The gate used no credential, network, provider, scheduler, copy, move, delete,
or public-consumer action. It does not establish that any file at the selected
path is operational. The former `data/market_data.sqlite` exists only in exact
historical registry projections and historical evidence; it is not a current
fallback, alias, or second operational market store.

## 2. Frozen contract and digests

| Evidence | Retained value |
| --- | --- |
| Gate contract | `quant_data.stage12a_market_v1_authority_gate` version `1.0.0` |
| Target profile | `stage12_market_v1_authority_coverage` |
| Canonical registry | schema `1.8.0`, revision `2.12.0` |
| Canonical registry source SHA-256 | `80a41e9f124cccb85bbdda665e33b1ef408f538b7e50b499b630b5ce6c761e7e` |
| Reconstructed pre-Stage 12 registry | schema `1.8.0`, revision `2.11.0`, source SHA-256 `f65f7d039b73037012c6183c34501027f4298376ab94125e856e9dd79c6d234d` |
| Stage 12 scope semantic SHA-256 | `8266f431519913879797a14ea0baa781b6c7e0dfe78365a5ff0107e386a0ad29` |
| Stage 12 scope file SHA-256 | `12e9a7380ad22c9595d922c1a53320f3c52e368cb688adf1d97bf4d7c9a18bf6` |
| Stage 12 roster SHA-256 | `a81f62a5fe3710011e1fe6eabfe7fcd483726a23f2dc9b0a7e592b4c78327fbb` |
| Path-free authority-gate evidence SHA-256 | `eb15eff3f6364fba47120d28e35995b8ef3a8e745673660bc2642135f30d9a0f` |
| Golden-file SHA-256 | `dfa399c58877430944cd07483ac78b1eba47155314412df825f05f09799652cd` |

The reconstructed `2.11.0` projection retains
`data/market_data.sqlite` only to reproduce pre-Stage 12 registry history.
The current canonical registry has only `data/market.sqlite` for the market
default. The scope semantic hash pins the closed normalized manifest; the scope
file hash pins its reviewed file bytes. The roster digest covers both symbols
and their frozen asset classifications.

## 3. Retained Stage 10 authority and coverage

Market v1 retains FMP provider-native daily OHLCV under price variant
`fmp_full_eod_v1`, with each symbol's earliest provider-returned history
through 2026-08-12. It freezes 629 instruments:

- 519 equities;
- 95 reviewed non-Russell ETFs; and
- 15 reviewed major indexes.

The source-only retained bindings are:

| Binding | SHA-256 or retained value |
| --- | --- |
| Stage 10 resume artifact | `1c0a9f941d829170e4085ef133df6343dadf32d975168ce43aa3724749553795` |
| Stage 10 scope manifest | `0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd` |
| Stage 10 failure manifest | `1b0b2a64ca302b1c5c411f927d625e852fd176a0d0aeebba20ba71ba9b9ebceb` |
| Stage 10 registry projection | schema `1.6.0`, revision `2.8.0`, source SHA-256 `c64aceefcc9a37cc0669398ea4d5817d997a5d82167941a204c2b77fffce77f9` |

The retained base reports 621 completed symbols and eight retained failures.
The retained extension closes 5,032 planned windows as 3,970 complete,
1,004 successful empty, and 58 authorized terminal outcomes across nine
tickers. The retained projection reports 4,235,893 current rows,
4,236,635 immutable versions, and zero missing current-version references.

These are receipt bindings and retained coverage facts, not a Stage 12A
reconciliation of a candidate database. The gate read only the expressly
permitted Stage 10 resume JSON metadata; it did not inspect a retained SQLite
candidate.

Market v1 does not claim historical constituent membership, a common history
start date, adjusted-price or corporate-action reconstruction, full U.S. or
delisted coverage, currency conversion, incremental refresh, operational
promotion, scheduling, public exposure, or that every terminal Stage 10 outcome
contains price history.

## 4. Deterministic offline gate

The implemented gate validates the exact closed Market v1 scope, canonical
four-store defaults, exact historical registry projections, and two independent
explicit absent-or-empty work roots. It emits strict JSON evidence that is
deterministic and path-free.

It rejects unknown fields, secrets, absolute or traversing store paths, altered
coverage counts, duplicate or reordered roster entries, opened or reordered
successor phases, nonempty roots, roots contained by the project, relative
roots, and identical or symlink-alias comparison roots. It records zero SQLite
store opens, filesystem mutations, credential-environment reads, network calls,
provider requests, subprocess calls, and scheduler actions.

Neither comparison root was created or changed. The resolver confirmed
`data/market.sqlite` from the canonical registry with an explicit empty
environment mapping, without opening either the current or former market
default.

## 5. Primary implementation and integration checks

| Check | Result |
| --- | --- |
| Worker Stage 12 scope and integration tests | 9 tests passed |
| Registry source-digest correction tests | 2 tests passed |
| Fresh two-root Stage 12A CLI | Exit status 0; evidence SHA-256 `eb15eff3f6364fba47120d28e35995b8ef3a8e745673660bc2642135f30d9a0f`; both supplied absent roots remained absent |
| Generated-artifact check | Clean |
| `git diff --check` | Clean |

The worker-focused commands were:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.market.test_stage12_scope tests.test_stage12_integration -v

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_stage11_registry -v
```

The independent CLI verification used two absent temporary roots:

```text
python3 -m quant_data --stage stage12a \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root /tmp/quant-data-stage12a-independent-20260815-a \
  --second-store-root /tmp/quant-data-stage12a-independent-20260815-b
```

## 6. Independent verification

Verifier: SolUltra
Verification date: 2026-08-15
Findings: none.

The focused independent suite passed 20 tests in 8.213 seconds:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests.market.test_stage12_scope tests.test_stage12_integration \
  tests.test_stage11_registry tests.test_stage10_registry \
  tests.test_registry_identifiers
```

The complete dependency-free suite passed 555 tests in 675.233 seconds with
FMP, BEA, EIA, and `QUANT_*` credential/path overrides unset:

```text
env -u FMP_API_KEY -u BEA_API_KEY -u EIA_API_KEY \
  -u QUANT_MARKET_DB_PATH -u QUANT_MACRO_DB_PATH \
  -u QUANT_COMPANY_DB_PATH -u QUANT_NEWS_DB_PATH \
  -u QUANT_SYSTEM_REGISTRY_PATH PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -t . -v
```

The generated-artifact check was clean. The Markdown check covered
39 files and 302 Markdown links; all relative targets resolved. The final
`git diff --check` was clean.

The full suite used isolated temporary SQLite fixtures and local loopback tests.
The verifier did not inspect any retained candidate or project-default SQLite
file, because Stage 12A forbids that action. Apart from the permitted Stage 10
resume JSON, it did not access retained store content. No verification action
used credentials, a provider, a network service, or a scheduler.

Unrelated dirty FMP-news work and the untracked `=1` entry were preserved and
are outside this evidence record.

## 7. Gate disposition

Stage 12A is **verified with stated limitations**. At completion it authorized
none of the following:

- Stage 12B incremental market collection;
- Stage 12C gap-only or newly authorized population;
- Stage 12D audited project-local operationalization, cutover, or repeated
  manual evidence; or
- Stage 12E scheduler proposal, installation, or start.

The bounded path decision is not an operational database decision. Stage 12B
was later separately authorized and completed only as an offline fixture
collector; [Stage 12B evidence](STAGE12B_EVIDENCE.md) records that successor
result. Stages 12C through 12E remain closed, and the prior
`data/market_data.sqlite` remains historical-only with no current alias or
fallback.
