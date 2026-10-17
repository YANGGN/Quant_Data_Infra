"""Source-reference hints and concise reconciliation for new extraction requests."""
VERSION = "transcript_analysis_prompt.v5"
MAX_OUTPUT_TOKENS = 32768
COURTESY_POLICY = "courtesy_only.v2"

RECONCILIATION_RULES = """
Final reconciliation, within this same request:
- The reference_index question inventory is authoritative for this request. The source.question_inventory remains the original legacy inventory for provenance. Never include excluded courtesy blocks as questions or topics.
- Use the supplied reference_index as a lookup for source roles, sections and question/answer exchanges. It is generated from source metadata, not an extra transcript or semantic interpretation. Read the complete source.turns.
- Give claims and candidates short descriptive IDs (for example capex_range, ethanol_timing, fermentation_refusal) rather than adjacent opaque numbers that are easy to swap. Keep each ID unique and stable while reconciling.
- For each included/merged candidate, compute the union of evidence turn IDs in claim_local_ids. Every candidate source_turn_id must be present in that union. If a cited turn supplies a real condition, timing change or caveat, retain it in the related claim's conditions/ambiguities AND evidence. Do not delete valid source turns merely to pass a reference check.
- For each topic, first compute the union of management_answer_turn_ids from its question_local_ids. Link only candidates intersecting that union. Check the candidate's summary and metric belong to this topic; similar IDs are not evidence of relevance. Every included/merged Q&A candidate needs a relevant topic link.
- Resolve metric attachment before combining statements: EPS guidance, segment operating profit, revenue and margins are distinct metrics. A condition on achieving the high end of EPS must stay on EPS; nearby operating-profit cadence is a separate claim. Do not transfer a condition across metrics.
- Preserve range flexibility, seasonality, mark-to-market assumptions, regulatory/export dependencies and later Q&A qualifications. Keep these on the claim they qualify. Preserve the full earliest-to-latest project window; a broad program's timing does not automatically apply to each plant or subset.
- Explicit future expense allocations and project-economics comparisons are forward candidates; retain them or explain a source-based exclusion.
- Do not turn medium-term growth wording into annual growth, or infer a flavors-only scope for a broader profit statement. Keep unclear scope/basis/period explicitly unresolved. Keep technology savings separate and flag possible overlap; do not add them to another savings program without explicit support.
- Reconcile all candidates and claims before writing concise evidence summaries. Avoid repeating full turns or the same caveat in multiple prose fields; retain distinct substantive claims and every necessary supporting turn. Never omit coverage or truncate the response to save tokens.
"""

REVIEW_RECONCILIATION_RULES = """
The reference_index question inventory is authoritative for this request. The source.question_inventory is retained legacy provenance. Excluded courtesy blocks must not become questions or ranked topics.
Check metric-specific attachment of conditions, retained range flexibility and timing windows, annual-versus-medium-term growth, uncertain segment scope, overlapping savings programs, and missing Q&A qualifications. Check candidate IDs against their summaries and source exchanges, not just their spelling. Structural validation cannot prove factual completeness or semantic accuracy.
"""


def reference_index(source):
    """Compact trusted source lookup; leave supplied source and input hash untouched."""
    from .transcript_analysis_contract import question_inventory
    turns = source.get("turns", [])
    inventory = (question_inventory(turns, courtesy_policy=COURTESY_POLICY) if turns else
                 {"courtesy_policy_version":COURTESY_POLICY,"question_blocks":[],"excluded_courtesy_blocks":[]})
    return {
        **inventory,
        "management_turn_ids": [t["turn_id"] for t in turns if t["speaker_role"] == "management"],
        "sections": {section:[t["turn_id"] for t in turns if t["section"] == section]
                     for section in ("prepared_remarks", "qa", "unknown")},
    }
