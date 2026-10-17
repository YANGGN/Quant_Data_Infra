"""Explicit Terra/high -> Sol/medium structured-call comparison; same source contract."""
from . import transcript_structured_call as baseline
from .transcript_structured_call import (
    BACKEND, MAX_OUTPUT_TOKENS, REQUEST_TIMEOUT_SECONDS, COURTESY_POLICY,
    FORMAT_NAMES, BRIEF_VERSION, REVIEW_VERSION, LENGTH_POLICY,
    brief_schema, review_schema, word_budget, render_brief, rendered_word_count,
    validate_brief, validate_editorial_review, expected_call, request_contract,
)
from .transcript_analysis_model import MAX_REQUEST_BYTES
from ..errors import ResourceLimitError
from ..json_codec import loads_strict, dumps_strict

EXTRACTOR = "gpt-5.6-terra"
EXTRACTOR_EFFORT = "high"
REVIEWER = "gpt-5.6-sol"
REVIEWER_EFFORT = "medium"
VERSION = "transcript_structured_call_terra_sol_prompt.v1"


def configuration_for(*, review=False):
    return {**baseline.configuration_for(review=review),
            "prompt_version": VERSION, "base_prompt_version": baseline.VERSION,
            "model": REVIEWER if review else EXTRACTOR,
            "reasoning_effort": REVIEWER_EFFORT if review else EXTRACTOR_EFFORT}


def make_request(source, *, analysis=None):
    request = loads_strict(baseline.make_request(source, analysis=analysis).decode())
    review = analysis is not None
    request["model"] = REVIEWER if review else EXTRACTOR
    request["reasoning"] = {"effort": REVIEWER_EFFORT if review else EXTRACTOR_EFFORT}
    raw = dumps_strict(request).encode()
    if len(raw) + 1024 > MAX_REQUEST_BYTES:
        raise ResourceLimitError("Complete comparison request exceeds bound; no text was truncated")
    return raw
