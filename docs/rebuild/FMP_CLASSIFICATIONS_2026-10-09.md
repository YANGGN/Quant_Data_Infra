# FMP sector and industry classifications

Accepted: October 9, 2026 (America/Toronto).

The user selected saved FMP profiles as the sole classification authority for
all 2,248 members of `major_index_liquid`. No CSV, Sharadar, SIC, GICS or inferred
fallback contributes to this mapping. Original source CSVs and prior evidence
remain immutable audit material, not competing canonical classifications.

## Ownership and use

`market.instrument_classifications` is a canonical market dataset, stored in
`data/market.sqlite`. Its append-only table is `market_classification_versions`;
`market_instrument_classifications` exposes one current row per instrument.
Use `source_symbol` for the selected ticker spelling and `provider_symbol` for
FMP's spelling; join prices on the existing `instrument_id`. FMP labels are
preserved exactly and must not be presented as official GICS classifications.
The old `instrument_classifications` table is the separate Stage 3 fixture
contract and is not the production mapping.

The internal bounded reader is
`quant_data.market.classifications.read_classifications(stores, symbols, as_of=...)`.
It accepts up to 2,248 unique explicit source symbols through an internal
StoreMap capability and returns explicit missingness, source IDs and warnings.
The initial storage change left public tool versions and their output contracts
unchanged. The October 10 addition below provides the supported local-agent
interface. Neither change adds external exposure, a writable interface or UI.

## Source and time semantics

The publisher uses only profiles already in `company_profile_evidence`, selected
through the saved description versions. It validates the original body hash,
JSON pointer, symbol, CIK, source mapping, current instrument association and
complete selected population under the existing physical market/company locks.
The company database is read-only. A missing/invalid label or identity mismatch
rejects the entire population before writes. No network or credentials are used.

Q and XOM retain the CIKs present in their original saved profile evidence,
including Q's missing source CIK and XOM's predecessor CIK. The separately
approved September 22 identity correction remains authoritative for current
issuer mappings. Original and current mapping IDs are both retained, and must
agree on the stable instrument and provider symbol; source CIK is checked
against the original immutable mapping, never rewritten to today's CIK.

`source_captured_at` is the profile capture time. `available_at` is this local
classification publication time. The mapping does not establish historical
source availability or a historical economic effective date. As-of reads exclude
versions published after their cutoff. Identical labels and stable identity
produce zero writes, including no new run/artifact/snapshot, even if profile
capture or mapping lineage has advanced. Changed classifications append versions;
prior versions and source evidence remain unchanged.

## Migration and authorized operation

Registry 2.96.0 adds only market ordinal 13,
`market:0013_fmp_classifications`, after ordinal 12. Earlier migration bytes and
schema objects remain unchanged. The exact 2.95.0 predecessor is preserved at
SHA-256 `61e06644d980ebf993ab44196c3794c457bba8cf6fe2b8fa582cc925081dc9ea`.
Public generated tool catalogs remain byte-identical. Dataset declaration and
registration use the existing mechanisms; no shared validation algorithm changes.

The finite operation is one additive market migration and one complete mapping
publication for exactly 2,248 names, from the pinned saved-profile preflight.
Provider requests: zero. No scheduler or refresh is installed, changed or run.
The local operation evidence is `.local/fmp-classifications-20261009/`.

## Validation

Impact: market-owned classification persistence and its bounded reader; company
profiles supply read-only input. Checks cover source hashes, identity aliases and
CIK corrections, missing labels, source changes, publication cutoff, revisions,
zero-write replay, transaction rollback, immutable rows, migration preservation,
checksum rejection, exact registry predecessor and generated-byte parity.
Adjacent checks cover the existing description publisher, Stage 10 price/ticker
readers, local tool CLI, and Inspector registry acceptance. A fresh independent
review preceded the canonical migration/publication. Completion evidence is
recorded below; fixture tests alone do not establish it.

## Completion evidence

Published at `2026-10-10T03:25:13.811527Z` (October 9 in America/Toronto),
then verified at `2026-10-10T03:29:33.576376Z`. The canonical current view and
version table each contain exactly **2,248** rows: **11 sectors and 140
industries**, all classified by FMP. Every label, stable identity and original
source reference matches the pinned retained-profile manifest. There are no
missing sector or industry values. The one successful ingestion run is
`classification_run_e899a65d5fa193b69d6c8874c749e423`; provider requests were zero.

Registry 2.96.0 has SHA-256
`77ae8d98df1c90a08697655669eca68c1d28e1788370a1a587d81e07cc27be20`.
The pinned input manifest has SHA-256
`9427f0deaf68e68a31d80fd414b8e62b588dde95ba896788397caf0475710a70`.
The migration ledger and all prior schema definitions were preserved exactly;
the separate Stage 3 fixture classification table remains empty. The migration
engine's mandatory complete foreign-key check passed before its ledger commit.

WSL became unresponsive during the original operation. The user explicitly
approved restarting WSL, including the interruption of running work. After
restart, the saved migration/publication receipts and committed database state
confirmed that both writes had already completed. Recovery performed **no
republication**: it reconciled the ledger, run, row counts, all source bytes and
lineage using physical locks and immutable readers. A focused post-publication
foreign-key check of `market_classification_versions` passed. The redundant
whole-store post-publication scan was interrupted and is not claimed as passed.
No recurring unit was manually triggered or reconfigured during recovery.

Validation completed 37 distinct unittest cases successfully: 12 classification,
11 company-description, five Stage 10 and nine local-agent CLI checks. A direct
Inspector check also passed for current registry acceptance, complete tool
inventory and unchanged fixture stores. The broader original test invocation
was interrupted while an existing test's setup traversed operational-data file
metadata; the selected remaining consumer checks ran in an isolated source
mirror without operational data. One existing Inspector test hard-codes registry
2.93.0 and fails on both the preserved 2.95.0 baseline and 2.96.0; this unrelated
stale assertion remains unchanged. The full suite was not required or run.

A fresh independent verifier reviewed the stable implementation, operation
script, contract parity, saved preflight and test evidence before publication,
with no blocking findings. Its review did not itself execute canonical work or
tests. Actual completion is supported by `migration-receipt.json`,
`publication-receipt.json` and `completion.json` in
`.local/fmp-classifications-20261009/`; the bounded recovery procedure is
`verify_recovery.py` in the same directory.

## Local-agent reader — October 10, 2026

Registry 2.97.0 / catalog 2.40.0 adds
`market.get_classifications@1.0.0`, discovered with the existing local launcher.
The exact 2.96.0 predecessor and every prior public tool contract/default are
preserved. There is no new migration, publication or provider workload.

An empty request selects all saved classification versions available by the
cutoff, one latest version per instrument, up to 2,248 names. This is the retained
classification population of `major_index_liquid`, not a claim of historical
index membership. Optional `tickers` selects 1–2,248 exact source spellings.
`sectors` (1–50) and `industries` (1–200) use unique exact FMP labels, OR within
each list and AND between lists. Filters apply after version selection. Unknown
requested tickers remain explicit even when filtering; nonmatching known names
are omitted with a count. Oversized results fail rather than truncate.

Optional `as_of` is an offset-aware local publication cutoff, bounded by the
host clock; default is now. Source capture and publication times remain separate.
Queries use only the existing market classification table, with the established
physical lock and immutable reader, exact migration/dataset identity checks and
host cancellation/deadline/operation bounds. Company evidence IDs are returned
as lineage without opening that store or introducing a fallback.

Responses include one source summary, ordered classification/missing records,
the standard receipt and warnings, an aggregate result lineage reference, and
each classification's original evidence/hash and canonical version IDs. The
full schema, examples and result decoding are in
[Local Agent Tools](../LOCAL_AGENT_TOOLS.md#fmp-sector-and-industry-classifications--october-10-2026).

### Reader validation and completion

The runtime now discovers and calls `market.get_classifications@1.0.0` in
registry `2.97.0` (SHA-256
`24c27b5189319eab448204ada3db1755214afc153a805f01ed8a275895f5eb4f`).
Catalog `2.40.0` has SHA-256
`e6e62f830d79c91ffb0fe2a9afc652fecb2a24e63aa8c2096297e994b4599629`.
The current inventory is 101 logical tools. The frozen v1 catalog is unchanged,
and removing the two new schemas reconstructs the exact prior v2 catalog.

Validation completed 37 distinct unittest cases: 12 public classification-tool
checks, 12 existing classification persistence checks, nine CLI checks and four
direct price/ticker-reader checks. The CLI's dynamic-inventory assertion was
updated to use the registered count after the additive tool increased it from
100 to 101; its correction check passed. The existing Inspector manifest endpoint
also accepted registry 2.97.0, advertised the new tool and left its fixture stores
unchanged. Adjacent checks ran in an isolated source mirror with no operational
data; the full suite and browser checks were not required or run.

Four real, read-only calls through the supported launcher verified the stored
mapping: the full universe returned 2,248 rows matching every saved source label
and reference; a batch returned four known names and one explicit missing ticker;
Technology plus Semiconductors returned 73 names; one microsecond before the
first publication returned no classifications. All responses retained source
metadata, receipts and warnings without truncation. No provider request,
database write, migration or repeated population occurred. Requests, response
digests, completion evidence and test logs are retained under
`.local/classification-tool-20261010/`.
