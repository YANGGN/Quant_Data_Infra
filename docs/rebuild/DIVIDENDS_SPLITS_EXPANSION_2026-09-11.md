# Dividends and splits expansion — September 11, 2026

The user explicitly requested implementation of the two outstanding dividends and
splits expansions for the selected 2,248-security universe. The missing-history
acquisition has finished, and expanded weekday monitoring was activated at
2026-09-11T15:25:56.453048+00:00. History is **assessed with source gaps**, not wholly complete.

## Verified scope and stored responses

| Dataset | Issuer-ready securities assessed | Canonical responses | Responses with events | Empty responses | Rejected source histories | Event rows in audited responses |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dividends | 2,201 | 2,157 | 1,326 | 831 | 44 | 111,561 |
| Splits | 2,201 | 2,201 | 1,120 | 1,081 | 0 | 4,518 |

An empty successful response is retained as source evidence; it is not proof
that a company has never paid a dividend or split. The other **47 selected
symbols** lack an eligible existing issuer identity. No identity was invented.
Previously stored populations outside the ready selection remain preserved.

The finite run made **3,394 GETs**: 1,707 dividends and 1,687 splits, with **zero
retries**, 19,154,615 response bytes, and no pending or uncertain requests.
It ran from 15:00:36 to 15:15:05 UTC. Its approved ceilings were 3,394 requests,
1 GiB and four hours. The run retained 1,008 previously populated ready endpoint
scopes without refetching them, and published 3,350 new successful source
responses. Its original result remains `processed_with_gaps`, exit 75, with 44
source failures. No failed outcome was rewritten as success.

This acquires the history returned by the existing FMP endpoints at `limit=1000`.
Every assessed response was below that row cap. It does not assert an unlimited
provider history or reassert completeness of earlier retained populations.

## Source exceptions and preservation

All 44 source failures were successful HTTP responses containing ambiguous
duplicate dividend events. Some duplicate dates have different cash amounts.
The existing strict parser rejected the entire affected response. Original bytes,
request/capture IDs and parser errors are retained; events were not summed,
selected arbitrarily, deduplicated or republished. Each rejection was reproduced
from its original bytes during audit. These symbols stay in normal daily scope.

Provider symbols with rejected dividend histories:

AFG, APLE, AXP, BBT, BLX, CAH, CARR, CB, CCL, CHH, CMCSA, CSX, CUBE, CVBF, DCOM, DD, DOC, DX, ESLT, FE, FER, FMBH, GBCI, HEI-A, HIMX, JHX, KIM, KRG, MKC, MOS, MS, O, OPRA, RJF, ROST, SIG, TAP, TCBK, TECH, THFF, THO, TTC, UCB, UVE.

Selected source symbols with issuer-identity gaps:

AIIR, AYA, BRAI, BSP, CNS, DOO, ELE, ERIC, ETOR, GFL, GGAL, GMAB, GRAB, GRFS, IQMX, JBS, MBC, MKSI, MMYT, NBN, NU, OMAB, OZK, PAX, PFBC, PICS, PSNY, PSQL, Q, QRVO, RIVN, RNW, SKHY, SLBT, SPHR, SRAD, STNE, TNET, TOWN, UROY, VICR, VIK, VNOM, WRD, XOM, XP, XPRO.

The immutable audit verified the exact partition of **4,402** ready endpoint
scopes: **4,358 canonical** and **44 rejected**. It checked charged attempts,
original response hashes, control and domain snapshots, and matching action
versions available at each capture time. Corrected snapshots may reuse earlier
versions; their memberships were not rewritten to imitate full response lists.
The audit verified 31,590,778 bytes of existing and new original evidence and
found **zero foreign-key violations** in the four action relations.

All **48,274** prior action versions and memberships, and **1,015** prior action
snapshots/artifacts, were preserved byte-for-byte at the row level. The run added
**68,163** action versions, bringing the action-version table to **116,437**.
No migration, registry, financial-statement or SEC fact change was made.

## Expanded weekday fetcher

The existing `quant-data-company-market-refresh.timer` remains enabled for
**Monday–Friday at 19:00 America/New_York**, with no catch-up or retry. The selected
actions lane requests both endpoints for all 2,201 ready symbols: at most
**4,402 GETs, 512 MiB and two hours** per normal invocation. Original responses
precede canonical publication; exact semantic replay causes no canonical change.
The existing legacy annual-estimates lane remains separately executed, with its
original roster and bounds. A failure in either lane does not suppress the other.
The combined service timeout is **151 minutes**.

The local actions activation receipt is this lane's explicit authority. The
shared `collection_bindings.json` bytes, modes and account allowance were left
unchanged, preserving other workers' pinned identities. The generic
`dividends_splits` binding therefore remains prepared; only this audited actions
lane is live through its private receipt gate. That gate requires the exact
population, a disjoint canonical/rejected evidence partition, and linked original
completion/audit/source-gap records. Capped responses, resource-limit failures,
provider failures, pending work and uncertain attempts do not qualify.

The shared FMP allowance remains 60,000/day, with a 6,000 maintenance reserve and
120 ms minimum account spacing. This reserve is not a guarantee that every
routine collection fits after heavy backfill usage; all lanes retain their
existing shared-quota failure accounting. No allowance was increased here.

Activation made zero provider requests and zero canonical writes. The new
operation parent's permissions were tightened from 0755 to 0700 after its
private-directory check stopped activation; the already validated staged receipt
was then published unchanged. Only the user manager's definitions were reloaded;
no collector was manually started. At 2026-09-11T15:26:20.663117+00:00, the timer was enabled and
waiting for **September 11 at 19:00 EDT**, and the loaded timeout was 151 minutes.
The service still displayed its prior failed-run state; this record does not
claim an observed successful expanded scheduled run.

## Implementation and validation

The new `quant_data/operations/selected_actions.py` provides bounded acquisition,
strict publication, original-byte checkpoints and the audited monitoring gate.
The existing company refresh routes actions into it while retaining estimates.
The existing fetch summary accepts the expanded numeric counts, and the prepared
service definition supplies the larger bounded timeout. No shared publisher,
identity, schema or parser semantics changed.

A private receipt/audit correction distinguished control snapshots from domain
snapshots. Original backfill receipts remain unchanged; the audit resolves their
control IDs using the existing publisher identity functions. Future receipts
provide both IDs, including on unchanged replay. The first read-only audit
stopped on that distinction before activation; the corrected audit passed.

Independent verification inspected **85 distinct passing focused cases**, the
frozen source hashes and actual audit evidence. The final action/replay suite
passed **16 tests**. Other passing saved runs cover legacy-lane failure isolation,
selection authority, shared allowance/parallel transport, source normalization,
cutoffs, publication deadlines and fetch-status accounting. An earlier command
used a nonexistent test module; another exposed three host-dependent fixtures.
The test selection and temporary host fixtures were corrected, and affected
checks passed. A full suite was not required for this additive controller using
unchanged shared semantics. No browser verification or provider repeats occurred.

Local evidence:

- `.local/actions-expansion-20260911/`: exact prepared plan, authorization, test logs,
  original/final review hashes, baseline row hashes, canonical audit, source-gap
  ledger and activation/service receipts.
- `data/.operations/collection/actions-selected/backfill-20260911/`: original
  charged attempts, raw bodies, outcomes, checkpoints and immutable exit-75 result.
- `data/.operations/collection/actions-selected/`: live activation, canonical audit,
  original history completion and exact source-gap ledger.

The next dataset expansion to backfill is **earnings dates/history**. Resolving
these 47 issuer identities and ambiguous dividend histories remains separate
follow-up work; neither is represented as completed history here.
