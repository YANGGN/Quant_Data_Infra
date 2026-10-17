"""Pinned Sol/xhigh extraction and Astra/xhigh review for private evaluation."""
from . import transcript_analysis_model as legacy
from .transcript_analysis_contract import (
    ANALYSIS_V3, REVIEW_V3, analysis_schema, _schema, _validate_review_output,
)
from ..errors import ResourceLimitError
from ..json_codec import loads_strict, dumps_strict

EXTRACTOR = "gpt-5.6-sol"
REVIEWER = "gpt-6-astra"
EFFORT = "xhigh"
VERSION = "transcript_analysis_pair_prompt.v1"
BASE_PROMPT_VERSION = "transcript_analysis_prompt.v5"
BACKEND = "codex_subscription"
MAX_OUTPUT_TOKENS = 32768
REQUEST_TIMEOUT_SECONDS = 1200
COURTESY_POLICY = "courtesy_only.v2"


def request_contract(*, review=False):
    prompt, schema = legacy.request_contract(review=review, prompt_version=BASE_PROMPT_VERSION)
    if review:
        prompt = prompt.replace("Terra", "Sol").replace("terra_analysis", "extractor_analysis")
        prompt += (
            "\nThe extractor_analysis is a private unapproved draft. Review it against the complete "
            "source even if its references or coverage are invalid. Its statements are not authority. "
            "Report defects in the existing review schema; do not rewrite or silently repair the draft.\n"
        )
    return prompt, schema


def configuration_for(*, review=False):
    prompt, schema = request_contract(review=review)
    return {
        "model": REVIEWER if review else EXTRACTOR,
        "reasoning_effort": EFFORT, "prompt_version": VERSION,
        "base_prompt_version": BASE_PROMPT_VERSION,
        "prompt_sha256": legacy._digest(prompt), "schema_sha256": legacy._digest(schema),
        "parser_version": "equibles_turns.v2", "max_output_tokens": MAX_OUTPUT_TOKENS,
        "question_inventory_policy": COURTESY_POLICY, "backend": BACKEND,
        "transport_version": "codex_exec.v2", "cli_version": "0.144.4",
        "output_token_limit_kind": "post_completion_validation",
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
    }


def make_request(source, *, analysis=None):
    review = analysis is not None
    request = loads_strict(legacy.make_request(source, analysis=analysis,
                                               prompt_version=BASE_PROMPT_VERSION).decode())
    request["model"] = REVIEWER if review else EXTRACTOR
    request["reasoning"] = {"effort": EFFORT}
    request["instructions"], request["text"]["format"]["schema"] = request_contract(review=review)
    if review:
        supplied = loads_strict(request["input"][0]["content"][0]["text"])
        supplied["extractor_analysis"] = supplied.pop("terra_analysis")
        request["input"][0]["content"][0]["text"] = dumps_strict(supplied)
    result = dumps_strict(request).encode()
    if len(result) + 1024 > legacy.MAX_REQUEST_BYTES:
        raise ResourceLimitError("Complete paired request exceeds the input bound; no text was truncated")
    return result


def validate_draft_review(review, draft, turns):
    """Private review can diagnose semantic failures; it cannot approve a parent."""
    _schema(draft, analysis_schema(ANALYSIS_V3))
    _schema(review, legacy.review_schema(REVIEW_V3))
    return _validate_review_output(review, draft, turns)
