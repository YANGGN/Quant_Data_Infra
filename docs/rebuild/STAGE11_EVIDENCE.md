# Stage 11 Evidence Record

Status: Completed and independently verified as a private, non-production
candidate. It is not promoted, publicly exposed, or scheduled.

## 1. Purpose and authorized boundary

Stage 11 is a bounded macro-store population inside the existing isolated
non-production Stage 10 cohort:

    /home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1

Its exact scope is:

- BEA NIPA table T10101, series A191RL, quarterly history;
- BEA NIPA table T10105, series A191RC, quarterly history;
- EIA monthly U.S. all-sector electricity retail sales, revenue, price, and
  customers; and
- EIA weekly series PET.WCESTUS1.W.

It authorizes no broader discovery, scheduling, promotion, retirement, tools,
dashboards, Atlas, exports, backup/restore exercise, full-corpus
reconciliation, or destructive operation.

The completed candidate remains private. No further BEA or EIA provider request
is required or authorized for this Stage 11 candidate.

## 2. Retained partial state and final completion state

The original retained resume state was
private/candidate/stage11/resume.json, file SHA-256
d14f8e15462af64e3b66d0013229341123433c4b74d5d84fda8fb7059aaa4e4f:

    bea_published=true; retail_published=true; weekly_published=false

weekly_published: false meant that the runner had not committed and
checkpointed the weekly publication phase. It did not mean that the provider
had no weekly observations or establish a provider failure.

The authorized final resume made exactly one EIA weekly logical request and
completed successfully. The final resume file records:

    bea_published=true; retail_published=true; weekly_published=true

Its file SHA-256 is
f9d60084456171b2ab08f2d1f13bf4c3d91abb546605302daccae6c54546cb9d.
The runner returned exit code 0 with resumed: true, bea_request_count: 0,
eia_retail_request_count: 0, and eia_weekly_request_count: 1. Thus it did not
repeat the completed BEA or retail phases.

The retained receipt identity is:

| Field | Value |
| --- | --- |
| Contract | quant_data.stage11_backfill_result version 1.0.0 |
| Target profile | stage11_bea_eia_macro_v1 |
| Scope manifest SHA-256 | 280d4056a2d6085449fef84c882165d53f89dc06541b4ce7611f8e33ea4f7cc1 |
| Historic receipt registry | schema 1.7.0, registry 2.9.0 |
| Historic registry source SHA-256 | 7e8ec6fc38d5a24962460d9c78a4df7754d3b29ad4b74c0c5e8ae807cd59ed56 |
| Completion file | private/candidate/stage11/completion.json |
| Completion file SHA-256 | c031d2641f233f02a58244c33bfa7c59d1cc5c236adf954c9133fd5740872e88 |
| Completion internal SHA-256 | 4c04903cb70f54c23d684b60a027ec9e8c07d70820018797334567e984963418 |
| Candidate receipt internal SHA-256 | 026741237695dc11f5021e1b20010b02d89438b7818be4ecf51a81ae866f8e46 |
| Candidate receipt file SHA-256 | db59b3d626698aa670aa2002efa33fe4e37b919f70a2c1525c64107d837e71d9 |
| Completion evidence SHA-256 | 6c1d3b68c5404ce1e1f245c55d6ecd2eee1a2f94f7b3520f4b0afcdb8671c0b8 |

The receipt state is private_candidate_only_no_operational_promotion.

## 3. Final targeted checks

The post-run macro-store SHA-256 is
73236866ccf5d3ff0ae28ca893a3f06ff05ced5a0fa9261495c81a03dbab3e0e.
Targeted checks reported:

| Relation family | Captures | Immutable versions | Current rows |
| --- | ---: | ---: | ---: |
| BEA NIPA | 2 | 635 | 635 |
| EIA retail | 1 | 1,136 | 1,136 |
| EIA weekly | 1 | 2,289 | 2,289 |

- integrity_check: ok;
- zero foreign_key_check rows;
- zero current-pointer anomalies;
- zero duplicate current keys; and
- zero stale-pointer counts across BEA, retail, and weekly relations.

These were targeted completion checks only. They did not perform an
unauthorized backup/restore exercise or full-corpus reconciliation.

## 4. Honest run timeline and corrective basis

On 2026-08-14, an initial exact invocation returned the sanitized
invalid_configuration result before any provider request because the keys were
not present in that WSL child process. The project .env had CRLF-related
shell-sourcing behavior; a line-ending-normalized child-process source made
BEA_API_KEY and EIA_API_KEY available without recording either value.

One separately authorized, schema-only weekly diagnostic then made exactly one
request for PET.WCESTUS1.W, with no retry and no response persistence. Its
HTTP 200 JSON response contained 2,289 rows of one exact eleven-field provider
shape. The closed parser was updated to recognize only that shape and series,
while retaining its bounds and sanitization checks.

The correction rests on a high-confidence provider facet/path mapping
inference: the requested compatibility-path series is PET.WCESTUS1.W, while
the exact provider row facet is WCESTUS1. It is not retroactive proof of the
later failed invocation's cause because that failure's provider body and
exception were not retained.

A later exact invocation returned a sanitized conflict before credential access
or a provider request because the target SQLite layout had been moved from the
authorized stores/ directory into the project data/ directory. The user
subsequently restored the required target store layout; no database content was
reconstructed, and the Stage 11 runner has no relocation path.

After exact-target, credential-presence, and dependency checks passed, the user
authorized one new weekly request. That final request is the sole weekly request
counted in the successful completion result above. The runner then published
the weekly phase, performed its targeted checks, and wrote the private
completion and candidate receipts.

## 5. Sidecar disclosure and independent verification

During post-run review, an independent verifier initially observed a zero-byte
macro.sqlite-wal and a 32,768-byte macro.sqlite-shm. It accidentally opened the
macro database once through a plain SQLite path; closing that connection removed
those pre-existing ephemeral sidecars. The main database SHA-256 remained
73236866ccf5d3ff0ae28ca893a3f06ff05ced5a0fa9261495c81a03dbab3e0e.
This observation must not be used to claim that the runner itself left no
sidecars.

Final independent verification passed on 2026-08-14. The bounded Stage 11 suite
ran 66 tests and the candidate-inspector suite ran 7 tests, all successfully.
Immutable actual-target checks reproduced the retained hashes and counts,
reported quick_check: ok and zero foreign-key, duplicate, current-pointer,
lineage, or vintage anomalies, and confirmed the private receipt has no job,
public-tool, dashboard, or export exposure.

The completion and candidate receipts do not persist physical-attempt counts.
The reported zero BEA, zero retail, and one weekly request result is corroborated
by the resume state, captures, timestamps, runner skip logic, and run report,
but is not independently provable from those receipts alone. Completion of this
private candidate does not authorize promotion, operational use, public
exposure, scheduling, or any further provider request.
