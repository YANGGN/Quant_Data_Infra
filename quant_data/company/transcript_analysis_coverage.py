"""Bounded guidance-coverage accounting; model judgment remains separately reviewed."""

def _ids(value, allowed, pointer, *, nonempty=False):
    from .transcript_analysis_contract import _bad
    if (nonempty and not value) or len(value) != len(set(value)) or not set(value) <= set(allowed):
        _bad("References must be unique, present and in the permitted scope", pointer)
    return set(value)


def validate_coverage(output, source):
    from .transcript_analysis_contract import _bad, _nonempty
    coverage = output["guidance_coverage"]
    management = {tid for tid,t in source.items() if t["role"] == "management"}
    reviewed = _ids(coverage["reviewed_management_turn_ids"], management,
                    "/guidance_coverage/reviewed_management_turn_ids")
    if reviewed != management:
        _bad("Every supplied management turn requires coverage accounting",
             "/guidance_coverage/reviewed_management_turn_ids")
    claims = {c["claim_local_id"]:c for c in output["guidance_claims"]}
    candidates = {}
    linked_claims = set()
    excluded = 0
    for i,item in enumerate(coverage["candidates"]):
        p = "/guidance_coverage/candidates/" + str(i)
        cid = item["candidate_local_id"]
        _nonempty(cid, p+"/candidate_local_id")
        if cid in candidates:
            _bad("Guidance candidate IDs must be unique", p+"/candidate_local_id")
        _nonempty(item["summary"], p+"/summary")
        turns = _ids(item["source_turn_ids"], management, p+"/source_turn_ids", nonempty=True)
        linked = _ids(item["claim_local_ids"], claims, p+"/claim_local_ids",
                      nonempty=item["disposition"] != "excluded")
        if item["disposition"] == "excluded":
            if linked:
                _bad("Excluded candidates cannot link claims", p+"/claim_local_ids")
            _nonempty(item["exclusion_reason"], p+"/exclusion_reason")
            excluded += 1
        else:
            if item["exclusion_reason"] is not None:
                _bad("Included or merged candidates have no exclusion reason", p+"/exclusion_reason")
            cited = {e["turn_id"] for c in linked for e in claims[c]["evidence"]}
            if not turns <= cited:
                _bad("Candidate source turns must be retained in its linked claim evidence", p+"/source_turn_ids")
            linked_claims.update(linked)
        candidates[cid] = (item, turns)
    if linked_claims != set(claims):
        _bad("Every guidance claim must be accounted for by a candidate", "/guidance_coverage/candidates")

    blocks = {q["question_local_id"]:q for q in output["analyst_focus"]["question_blocks"]}
    all_answers = {tid for q in blocks.values() for tid in q["management_answer_turn_ids"]}
    topic_links = set()
    for i,topic in enumerate(output["analyst_focus"]["topics"]):
        p = "/analyst_focus/topics/" + str(i) + "/guidance_candidate_ids"
        links = _ids(topic["guidance_candidate_ids"], candidates, p)
        answers = {tid for q in topic["question_local_ids"] for tid in blocks[q]["management_answer_turn_ids"]}
        for cid in links:
            if not candidates[cid][1] & answers:
                _bad("Topic guidance candidates must reference that topic's management answers", p)
        topic_links.update(links)
    required = {cid for cid,(item,turns) in candidates.items()
                if item["disposition"] != "excluded" and turns & all_answers}
    if not required <= topic_links:
        _bad("Q&A guidance candidates must be linked from a relevant analyst topic", "/analyst_focus/topics")
    return {"reviewed_management_turn_count":len(reviewed),"candidate_count":len(candidates),
            "excluded_candidate_count":excluded,"linked_claim_count":len(linked_claims),
            "qa_candidate_count":len(required),"linked_topic_candidate_count":len(topic_links)}


def validate_finding(finding, output, pointer):
    from .transcript_analysis_contract import _bad, _nonempty
    claims = {g["claim_local_id"] for g in output["guidance_claims"]}
    related = _ids(finding["related_claim_local_ids"], claims, pointer+"/related_claim_local_ids")
    kind = finding["finding_type"]
    missing = finding["missing_information"]
    if kind in {"missing_claim","missing_qualification"}:
        if finding["category"] not in {"guidance","coverage"}:
            _bad("Missing-claim and qualification findings refer to guidance coverage",pointer+"/category")
        _nonempty(missing,pointer+"/missing_information")
        _nonempty(finding["proposed_correction"],pointer+"/proposed_correction")
    elif missing is not None:
        _nonempty(missing,pointer+"/missing_information")
    if kind == "missing_qualification" and not related:
        _bad("A missing qualification must identify its existing guidance claim",pointer+"/related_claim_local_ids")
    if kind == "acceptable_exclusion" and finding["severity"] == "error":
        _bad("An acceptable exclusion cannot be a blocking error",pointer+"/severity")
