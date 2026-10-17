"""Structured coverage and actionable review instructions, preserving prior requests."""
VERSION = "transcript_analysis_prompt.v4"

COVERAGE_RULES = """
Structured coverage workflow:
- Inventory numeric/qualitative forecasts, operating plans, assumptions, qualifications and explicit refusals across every supplied management turn. guidance_coverage.reviewed_management_turn_ids lists all management turn IDs once, even turns without guidance.
- Record each distinct candidate with a concise summary, source_turn_ids and disposition. included/merged candidates must link claim_local_ids; excluded candidates have no claims and a specific exclusion_reason. Every guidance claim needs at least one candidate. Do not list every historical sentence; record plausible forward candidates and meaningful exclusions. Silence is not refusal.
- Reconcile repeated mentions BEFORE finalizing claims. Map later assumptions, timing, caveats and refinements to the same claim and retain all candidate source turns in that claim's evidence. Keep different metrics/scopes/horizons separate. Every candidate source_turn_id must appear in at least one linked claim's evidence. Avoid duplicate guidance rows for repetitions.
- Each analyst topic has guidance_candidate_ids: link every forward-looking element in its response summary to a candidate, including explained exclusions. A linked candidate must share a management-answer turn with that topic. Every included/merged Q&A candidate belonging to an observed answer must be linked from a relevant topic; topics with no forward outlook use [].
- Preserve conditions on EPS/ranges, capex flexibility, later timing refinements, qualitative capacity/savings plans, and explicit refusals to quantify. Shared savings components are not automatically incremental. Generic optimism can be excluded. Nearby explicit outlook context can supply period wording; do not infer unsupported fiscal dates or annual growth cadence.
- Keep summaries concise, avoid restating the transcript and reconcile references before returning. This is one full-call analysis, not an additional request.
"""

PRECISE_REVIEW_RULES = """
Classify every finding using finding_type. missing_claim identifies absent guidance; missing_qualification identifies absent conditions/timing/caveats on existing related_claim_local_ids. Supply missing_information and proposed_correction for both. Use interpretation_error, incorrect_number, coverage_gap, source_ambiguity, acceptable_exclusion or other when appropriate. related_claim_local_ids must exist; [] is allowed for a genuinely new claim. Acceptable exclusions are advisory warnings, never blocking errors.
Independently check source coverage against the candidate inventory and the final claims. Check Q&A/claim links and merging of repeated mentions. Structural completeness of the checklist is not proof that Terra found every substantive outlook. Do not request fields outside the agreed schema or manufacture forecasts from historical data, market-size estimates, analyst hypotheses or broad aspirations.
"""
