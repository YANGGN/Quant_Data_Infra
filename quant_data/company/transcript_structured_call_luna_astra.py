"""Luna/high extraction with an independent, tiered Astra/high review."""
from . import transcript_structured_call_terra_sol as baseline
from . import transcript_structured_quality as quality
from .transcript_structured_call_terra_sol import (
    BACKEND, MAX_OUTPUT_TOKENS, REQUEST_TIMEOUT_SECONDS, COURTESY_POLICY,
    BRIEF_VERSION, brief_schema, expected_call, word_budget, render_brief,
    rendered_word_count,
)
from .transcript_structured_quality import (
    REVIEW_VERSION, LENGTH_POLICY, review_schema, validate_brief,
    validate_editorial_review, assess_brief,
)
from .transcript_analysis import digest
from .transcript_analysis_model import MAX_REQUEST_BYTES
from ..errors import ResourceLimitError
from ..json_codec import loads_strict, dumps_strict

EXTRACTOR = "gpt-5.6-luna"
EXTRACTOR_EFFORT = "high"
REVIEWER = "gpt-6-astra"
REVIEWER_EFFORT = "high"
VERSION = "transcript_structured_call_luna_astra_prompt.v1"
FORMAT_NAMES = ("transcript_structured_luna", "transcript_structured_astra_review")
REVIEW_PROMPT = quality.REVIEW_PROMPT.replace("ORIGINAL Terra draft", "ORIGINAL Luna draft") + """
Our project needs a highly informative, concise structured digest capturing the essence of the call,
not an exhaustive transcript reconstruction. Assess the ORIGINAL draft's usefulness before corrections.
Do not penalize an empty section if its important substance is already captured elsewhere. An omission
is material only if it loses a central call theme or changes the meaning of a selected claim; optional
detail and editorial preferences are not blockers. Distinguish source ambiguity from extractor mistakes.
Use editorial_notes to state your final verdict on the ORIGINAL draft: usable as is, usable with caveats,
or needs targeted corrections. Explain concision, coverage of the call's essence, and factual fidelity
briefly. Any corrected brief is a separate candidate; it must not hide defects in the original Luna output.
"""


def request_contract(*, review=False):
    return (REVIEW_PROMPT, review_schema()) if review else baseline.request_contract()


def configuration_for(*, review=False):
    base = quality if review else baseline
    prompt, schema = request_contract(review=review)
    return {**base.configuration_for(review=review), "prompt_version": VERSION,
            "model": REVIEWER if review else EXTRACTOR,
            "reasoning_effort": REVIEWER_EFFORT if review else EXTRACTOR_EFFORT,
            "prompt_sha256": digest(prompt), "schema_sha256": digest(schema)}


def make_request(source, *, analysis=None):
    review = analysis is not None
    base = quality if review else baseline
    request = loads_strict(base.make_request(source, analysis=analysis).decode())
    request["model"] = REVIEWER if review else EXTRACTOR
    request["reasoning"] = {"effort": REVIEWER_EFFORT if review else EXTRACTOR_EFFORT}
    prompt, schema = request_contract(review=review)
    request["instructions"] = prompt
    request["text"]["format"].update(name=FORMAT_NAMES[int(review)], schema=schema)
    raw = dumps_strict(request).encode()
    if len(raw) + 1024 > MAX_REQUEST_BYTES:
        raise ResourceLimitError("Complete Luna/Astra request exceeds bound; no text was truncated")
    return raw
