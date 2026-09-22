# Company short descriptions - September 22, 2026

## Scope and authority

The user requested a saved short description for every selected ticker, reusing
retained information and obtaining missing information from FMP. This authorizes
one finite population of the existing 2,248-security major_index_liquid selection,
an additive company-store migration, and at most 516 single-attempt FMP
/stable/profile requests. It adds no recurring collection or public endpoint.

The frozen plan reuses 1,732 retained nonempty FMP descriptions and requests the
516 missing profiles. It preserves the selected-symbol/provider-symbol mappings
(including class-share aliases), instrument IDs, CIKs, membership and mapping
snapshots. Network acquisition finishes before publication locks/transactions.
The existing FMP_API_KEY resolver, bounded HTTPS worker, shared account allowance,
physical locks, immutable readers, migration engine and ingestion coordinator
remain authoritative.

Limits: 516 requests total, no hidden retry, 1 MiB per response, 32 MiB aggregate,
30 seconds per request and 1,800 seconds across the acquisition. Attempt markers
precede dispatch; an uncertain/failed attempt cannot be repeated. Auth, quota and
server-error responses stop the operation. Original responses remain private.

## Storage and derivation

Registry 2.91.0 adds company:0019_short_descriptions after company 0018.
Earlier migration bytes remain unchanged. The company.profile_evidence dataset
owns company_profile_evidence and retains full raw source JSON, digest, original
source pointer, capture timestamp and retained-evidence reference.

The derived company.short_descriptions dataset owns
company_short_description_versions and its current view company_short_descriptions.
The requested field is short_description. Full source description, instrument ID,
selected ticker, CIK, source profile, input snapshots, method, truncation flag,
publication availability and predecessor remain attached to each version.

opening_sentence_excerpt.v1 normalizes whitespace and extracts the opening
business sentence without splitting common company/geography abbreviations.
When that opening has fewer than 12 words, the next source sentence is included
to explain a terse opening such as a generic holding-company statement.
It is capped at 400 characters; a longer sentence ends at a word boundary with
an explicit ellipsis and excerpt_truncated=1. It is a source excerpt, not an
independently verified or model-generated company assessment.

If a newly fetched FMP profile lacks description but supplies companyName and
industry, industry_template.v1 explicitly states that company operates in that
industry. If neither description nor those source fields are usable, publication
reports a gap rather than inventing business facts. Symbol or CIK conflicts also
remain explicit gaps.

Availability is local derivation/publication time. Reusing September 10 source
evidence does not make the new derived text available on September 10. Later
corrections append versions. Repeating the same semantic content writes zero
runs, artifacts or versions; a real A -> B -> A revision appends.

The bounded Python read_descriptions service reads exact tickers through the
existing immutable reader with an explicit as_of cutoff. No new public tool,
UI, scheduler, hosting or operational route is introduced.

## Operation and validation

The fixed private operation is quant_data.operations.company_short_descriptions.
Its prepare, acquire, inspect and publish actions retain the original plan,
attempt markers, responses and publication receipt under
data/.operations/company-short-descriptions/20260922.
A run has not occurred merely because these files or declarations exist.

Focused offline checks cover alias identity, only-missing acquisition,
credential-free response reuse, abbreviation handling, length limits,
explicit industry fallback, wrong symbols/CIKs, future evidence, immutable rows,
version transitions, zero-write replay, uncertain-attempt and quota stops,
migration preservation/checksum rejection, and predecessor compatibility.
Adjacent checks cover the reused FMP transport/account allowance, existing
company publisher, and unchanged tool catalog. Live completion evidence will
be appended after the bounded population and immutable postcheck.

Reference: [FMP company profile API](https://site.financialmodelingprep.com/developer/docs/stable/profile-symbol).

## Completed population

The publication at 2026-09-22T19:11:00.643053Z saved **2,248 of 2,248** selected-security
short descriptions, with no gaps. It reused **1,732** retained descriptions and
made exactly **516** FMP profile requests: all returned HTTP 200, totaling
**1,386,226 bytes**. No retries occurred.

All rows use opening_sentence_excerpt.v1; no industry-only fallback was needed.
Descriptions contain 71-399 characters. Seven longer excerpts end with an
explicit ellipsis and have excerpt_truncated=1. All original full descriptions
and exact raw profile bytes are retained.

Company migration company:0019_short_descriptions is applied at registry 2.91.0,
SHA-256 7c734a408c2407e86cac7fc1baac12726cb6839112d9d10cd70339b3902701d3.
The migration's full foreign-key gate completed. Its postcheck preserved every
earlier company migration row and SQL definition, and the observed prior source
counts: 2,217 issuers; 963,552 FMP research rows; 45,290 transcripts; and 48,963
structured transcript outputs. WSL temporarily stopped accepting new commands
during that validation. The original migration completed; this task did not
restart WSL or repeat the migration.

The immutable completion audit at 2026-09-22T19:12:16.396837Z verified all
2,248 saved identities and descriptions against the prepared inputs and every
raw evidence BLOB against its source bytes and SHA-256. Both new tables pass
foreign-key checks. The publication created 2,248 profile-evidence rows,
2,248 description versions, 2,248 ingestion artifacts, one ingestion run and
one snapshot. Replaying the full batch returned unchanged with written_count=0;
the description/evidence state and scoped ingestion counts were unchanged.

The focused/adjacent validation covers 48 distinct cases after the intended
registry-version and excerpt-expectation updates; applicable failed expectations
were rerun and passed. The earlier mapping fixture run also passed its 18
existing mapping cases. The generated-artifact check passed, and a fresh
independent reviewer cleared the stable migration/population baseline and the
small excerpt-quality amendment. No unrelated full-suite certification is
claimed. No scheduler, UI, hosting, commit or push action occurred.

Evidence:
- data/.operations/company-short-descriptions/20260922/plan.json
- data/.operations/company-short-descriptions/20260922/responses/
- data/.operations/company-short-descriptions/20260922/publication.json
- .local/company-short-descriptions-20260922/migration-result.json
- .local/company-short-descriptions-20260922/prepared-summary.json
- .local/company-short-descriptions-20260922/completion-receipt.json
- .local/company-short-descriptions-20260922/review-final-baseline.json
