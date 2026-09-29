# Selected price history backfill — September 10, 2026

**Later completion:** The September 11 user-authorized continuation resolved all ten mappings, settled the five held histories with explicit quarantine, and activated the expanded seven-day refresh. See [final completion and activation](PRICE_HISTORY_COMPLETION_2026-09-11.md). The dated observations below remain preserved as historical evidence.

Final read-only audit completed September 11 at 00:03:20 UTC (September 10 local time).

- Selected source universe: **2,248 symbols**.
- Supported FMP identities: **2,238**.
- Histories completed to the provider boundary: **2,233**.
- Held histories: **5**; full-universe historical completion is not claimed.
- New canonical price rows: **9,709,999**, across **11,254 published windows**.
- History cutoff: **2026-09-09**. Existing history and ETF/index selections are retained.
- Aggregate reservations: **11,284** of 13,872; received bytes: **2,144,965,109**.
- Original uncertain-response reserve remains **335,544,320 bytes**; no charge or byte refund.
- Explicit manual recovery: **24 GETs**, no automatic retries; all 20 interrupted responses recovered.
- All owned workers have exited; the shared FMP account has no pending/stopped attempt.
- The ordinary daily window remains **seven calendar days**. Expanded activation is held pending the data-gap decision.

## Held provider windows

| Symbol | Held window | Known conflicting dates | Stored history range |
| --- | --- | ---: | --- |
| BNY | 1970-01-01–1974-12-31 | 1 | 1975-01-02–2026-09-09 |
| DFTX | 2017-01-01–2021-12-31 | 1 | 2022-01-03–2026-09-09 |
| HE | 1987-01-01–1991-12-31 | 31 | 1992-01-02–2026-09-09 |
| RCAT | 2017-01-01–2021-12-31 | 1 | 2022-01-03–2026-09-09 |
| SENEB | 1987-01-01–1991-12-31 | 211 | 1992-01-02–2026-09-09 |

The five original windows remain unpublished as complete windows; older windows below their held cursors have not been traversed. The 245 dates are observed conflicts, not a claim that these are the only missing dates. BNY has two conflicting records for one date; the other cases have internally inconsistent OHLC. Raw responses are retained intact. Four provider-conflict checks returned the same conflicts; SENEB’s older conflict was newly encountered in the final continuation.

SENEB’s modern history separately contained two literal null percentage-change values. The historical adapter now preserves those nulls while validating mandatory OHLCV and all other fields. Its original 1,119-row response was published without another GET. This does not relax the older OHLC conflicts.

## Decision pending

Recommended: retain raw evidence, quarantine invalid/conflicting dates, publish remaining valid rows, finish untouched older windows, and permit the expanded seven-day refresh with explicit gap records. No such data-disposition or activation change has been made while the user’s decision is pending.

## Identity gaps

ARWR, AUR, BF.A, BH.A, GEF.B, HEI.A, INTR, LEN.B, MOG.A, UHAL.B. These ten source symbols remain outside the frozen supported FMP population; they are not counted as completed histories.

## Validation and evidence

The 31 focused historical/pipeline/live-price/quota checks passed. Independent source review covered the historical-null correction and the fixed recovery/finishing drivers. A grouped audit exceeded its 90-second read limit before recovery publication; an equivalent indexed query passed isolated alias/missingness/filter checks and independent review, then the actual immutable audits passed. No exhaustive-suite pass is claimed.

- `data/.operations/collection/daily-price-backfill-20260910/longest-history/recovered-history/completion-review.json`
- `data/.operations/collection/daily-price-backfill-20260910/longest-history/recovered-history/settled-canonical-audit.json`
- `data/.operations/collection/daily-price-backfill-20260910/longest-history/manual-recovery/verified-provider-conflicts.json`
- `data/.operations/collection/daily-price-backfill-20260910/longest-history/recovered-history/seneb-older-validation.json`
- `.local/universe-rollout-20260909/parallel-validation/`
