"""Model-facing v2 constraints; canonical output and validation remain v1."""
from copy import deepcopy

VERSION = "transcript_analysis_prompt.v2"

EXTRACTION_RULES = """
Encoding rules for the supplied schema:
- Numeric values retain the written magnitude; scale is a positive decimal multiplier, not a count of decimal places. Use scale "1" for unscaled EPS, counts, ratios and percentage units, "1000" for thousands, "1000000" for millions, and "1000000000" for billions. For example, "$1.3 to $1.5 billion" means low "1.3", high "1.5", scale "1000000000"; "$250 million" means point "250", scale "1000000"; "$5.15 to $5.60 per share" means low "5.15", high "5.60", scale "1". Do not double-scale or use scale "1" with unexpanded million/billion magnitudes.
- A point uses only point; a range uses only low/high; a lower bound uses only low; an upper bound uses only high. All other numeric slots are null. "At least one" is lower_bound, low "1", inclusive, scale "1", never point "1". All numeric shapes require scale, even unscaled values. Qualitative/none shapes have null point/low/high/scale. Withdrawn/not_provided guidance uses shape none.
- Fiscal labels are nullable and require explicitly supported fiscal interpretation. Only period_kind fiscal_year or fiscal_quarter may contain fiscal_year. Only fiscal_quarter may contain fiscal_quarter. Calendar, multi_year, other and unspecified targets have both fiscal fields null; retain all year/quarter wording in period_text. "Second half of 2026" and "three to five years starting 2025" must not receive a single fiscal-year label when period_kind is other/multi_year. Never infer the target from the transcript's own quarter.
- One guidance claim has one metric. Split flavors growth and operating-profit growth into separate claims even when supported by the same quotation.
- Copy the trusted question inventory exactly. A topic's question_evidence must come only from its referenced question blocks' analyst_turn_ids; its answer_evidence must come only from those same blocks' management_answer_turn_ids. Prepared remarks and another topic's unreferenced exchange cannot support a topic answer. Include another question block in the topic only if that question genuinely belongs to it; otherwise remove the unrelated citation and narrow the response summary.
- Reconcile values with source_value_text and evidence, fiscal labels with period_kind, and every topic citation with the union of its member blocks before returning. Preserve uncertainty; never repair a mismatch by inventing a value, quote or question membership.
"""

REVIEW_RULES = """
Explicitly check numeric scale against the quoted million/billion wording and multiply the returned magnitude by scale when assessing financial amounts. Check unscaled numeric values have scale "1", bound slots match shape, non-fiscal/multi-year targets have no inferred fiscal labels, and each guidance claim has one metric. Check every topic question/answer citation belongs to that topic's referenced source exchanges. Report these as errors when wrong; structurally valid output is not necessarily economically correct.
"""


def extraction_schema(base):
    """Express existing shape/period rules with supported nested anyOf branches."""
    schema = deepcopy(base)
    value = schema["$defs"]["value"]
    branches = []
    for shape, slots in (
        ("point", ("point",)), ("range", ("low", "high")),
        ("lower_bound", ("low",)), ("upper_bound", ("high",)),
        ("qualitative", ()), ("none", ()),
    ):
        branch = deepcopy(value)
        properties = branch["properties"]
        properties["shape"] = {"type": "string", "enum": [shape]}
        for slot in ("point", "low", "high"):
            properties[slot] = (deepcopy(value["properties"][slot]["anyOf"][0])
                                if slot in slots else {"type": "null"})
        properties["scale"] = ({
            "type": "string",
            "pattern": r"^(?:[1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$",
            "description": 'Positive numeric multiplier: 1 unscaled, 1000 thousand, 1000000 million, 1000000000 billion. Keep the written magnitude in numeric slots.',
        } if slots else {"type": "null"})
        properties["qualitative_text"] = (
            {"type": "string"} if shape == "qualitative" else {"type": "null"})
        if slots:
            properties["source_value_text"] = {"type": "string"}
        branches.append(branch)
    schema["$defs"]["value"] = {"anyOf": branches}
    target = schema["$defs"]["guidance_claim"]["properties"]["target"]
    periods = []
    for kinds, fiscal, quarter in (
        (["fiscal_year"], True, False), (["fiscal_quarter"], True, True),
        (["calendar_year", "calendar_quarter", "multi_year", "other", "unspecified"], False, False),
    ):
        branch = deepcopy(target)
        properties = branch["properties"]
        properties["period_kind"] = {"type": "string", "enum": kinds}
        properties["fiscal_year"] = (
            {"anyOf": [{"type": "integer", "minimum": 1900, "maximum": 2200}, {"type": "null"}]}
            if fiscal else {"type": "null"})
        if not quarter:
            properties["fiscal_quarter"] = {"type": "null"}
        periods.append(branch)
    schema["$defs"]["guidance_claim"]["properties"]["target"] = {"anyOf": periods}
    topic = schema["$defs"]["analyst_topic"]["properties"]
    topic["question_evidence"]["description"] = "Only analyst turns belonging to question_local_ids in this topic."
    topic["answer_evidence"]["description"] = "Only management_answer_turn_ids belonging to question_local_ids in this topic; no prepared remarks or unrelated exchanges."
    return schema
