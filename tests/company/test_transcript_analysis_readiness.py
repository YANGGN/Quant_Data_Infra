from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from . import test_transcript_analysis_contract as contract_fixtures
from . import test_transcript_analysis_pipeline as fixtures
from quant_data.company import transcript_analysis_model as model
from quant_data.company.equibles_transcripts import EquiblesTranscriptPublisher, RawPage
from quant_data.company.transcript_analysis import read_source
from quant_data.company.transcript_analysis_contract import (
    question_inventory, validate_analysis, validate_review,
)
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.equibles_transcript_backfill import atomic
from quant_data.operations.transcript_analysis_pilot import TranscriptExtractionPilot


def response_with_usage(output, output_tokens, *, review=False):
    return dumps_strict({
        "id": "resp_readiness_fixture",
        "model": model.REVIEWER if review else model.EXTRACTOR,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [{
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": dumps_strict(output)}],
        }],
        "usage": {
            "input_tokens": 100,
            "output_tokens": output_tokens,
            "total_tokens": 100 + output_tokens,
        },
    }).encode()


class PilotTransport(fixtures.Transport):
    backend = "codex_subscription"

    def __init__(self, values):
        super().__init__(values)
        self.preflights = 0

    def _deadline_seconds(self):
        return 1200

    def prepare_credentials(self):
        self.preflights += 1


class TranscriptReadinessContractTests(unittest.TestCase):
    SOURCE = {
        "capture_id": "fixture",
        "input_sha256": "1" * 64,
        "turns": [],
    }
    SOL_V4_VECTORS = (
        {
            "backend": "openai_api",
            "configuration": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "prompt_version": "transcript_analysis_prompt.v4",
                "prompt_sha256": "d9e076e113f16178aa63e0882f5c77d4877e9dcdcfe7cd5467baf1d79a217a59",
                "schema_sha256": "3bab09a3ea920f44ff226941d4effb6eba39590a7e3ab33a0a040d84fa11bf2b",
                "parser_version": "equibles_turns.v2",
                "max_output_tokens": 16384,
            },
            "request_identity": "275287f4fe55ee6b0f11ff7b98e591dcd78394d6dec4365daea25384884c0afa",
            "request_sha256": "4645d3bab90193da6d47f1e46598aec59dc0dff84c6950ee1fda49ab991f4f7f",
        },
        {
            "backend": "codex_subscription",
            "configuration": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "prompt_version": "transcript_analysis_prompt.v4",
                "prompt_sha256": "d9e076e113f16178aa63e0882f5c77d4877e9dcdcfe7cd5467baf1d79a217a59",
                "schema_sha256": "3bab09a3ea920f44ff226941d4effb6eba39590a7e3ab33a0a040d84fa11bf2b",
                "parser_version": "equibles_turns.v2",
                "max_output_tokens": 16384,
                "backend": "codex_subscription",
                "transport_version": "codex_exec.v1",
                "cli_version": "0.144.4",
                "output_token_limit_kind": "post_completion_validation",
                "request_timeout_seconds": 1200,
            },
            "request_identity": "0e5025a5a90df2fae3fe49090de2a915dac841a6db698bed312b250f190131ff",
            "request_sha256": "4645d3bab90193da6d47f1e46598aec59dc0dff84c6950ee1fda49ab991f4f7f",
        },
    )

    def test_v5_raises_only_new_requests_and_reference_index_is_non_mutating(self):
        source = {
            "capture_id": "fixture",
            "input_sha256": "a" * 64,
            "turns": [],
            "question_inventory": {"question_blocks": [], "excluded_courtesy_blocks": []},
        }
        before = deepcopy(source)
        request = json.loads(model.make_request(source))
        supplied = json.loads(request["input"][0]["content"][0]["text"])
        self.assertEqual(request["max_output_tokens"], 32768)
        self.assertEqual(model.configuration_for()["max_output_tokens"], 32768)
        self.assertEqual(supplied["reference_index"]["courtesy_policy_version"], "courtesy_only.v2")
        self.assertEqual(source, before)
        for version in (
            model.LEGACY_PROMPT_VERSION,
            model.PREVIOUS_PROMPT_VERSION,
            model.SUMMARY_PROMPT_VERSION,
            model.COVERAGE_PROMPT_VERSION,
        ):
            with self.subTest(version=version):
                legacy = json.loads(model.make_request(source, prompt_version=version))
                legacy_supplied = json.loads(legacy["input"][0]["content"][0]["text"])
                self.assertEqual(legacy["max_output_tokens"], 16384)
                self.assertEqual(model.configuration_for(prompt_version=version)["max_output_tokens"], 16384)
                self.assertNotIn("reference_index", legacy_supplied)

    def test_v2_courtesies_preserve_boundaries_and_drive_rank_counts(self):
        source = contract_fixtures.turns()
        source.extend([
            {"turn_id": "t12", "speaker_id": "a3", "speaker_role": "analyst", "section": "qa",
             "text": "Thank you so much."},
            {"turn_id": "t13", "speaker_id": "m1", "speaker_role": "management", "section": "qa",
             "text": "You are welcome."},
            {"turn_id": "t14", "speaker_id": "a4", "speaker_role": "analyst", "section": "qa",
             "text": "Good morning. Congratulations on the quarter."},
            {"turn_id": "t15", "speaker_id": "m1", "speaker_role": "management", "section": "qa",
             "text": "Good morning."},
            {"turn_id": "t20", "speaker_id": "a5", "speaker_role": "analyst", "section": "qa",
             "text": "Wonderful. Thank you so much."},
            {"turn_id": "t21", "speaker_id": "m1", "speaker_role": "management", "section": "qa",
             "text": "Thank you."},
            {"turn_id": "t30", "speaker_id": "a6", "speaker_role": "analyst", "section": "qa",
             "text": "Great. Appreciate the caller and congrats again."},
        ])
        legacy = question_inventory(source)
        current = question_inventory(source, courtesy_policy="courtesy_only.v2")
        self.assertEqual(legacy["excluded_courtesy_blocks"], [["t12"]])
        self.assertEqual(
            current["excluded_courtesy_blocks"], [["t12"], ["t14"], ["t20"], ["t30"]]
        )
        checked = validate_analysis(
            contract_fixtures.model(), source, courtesy_policy="courtesy_only.v2"
        )
        self.assertEqual(checked["derived"]["courtesy_policy_version"], "courtesy_only.v2")
        self.assertEqual(checked["derived"]["excluded_courtesy_block_count"], 4)
        self.assertEqual(checked["derived"]["total_question_block_count"], 2)
        self.assertEqual(checked["derived"]["ranked_topics"][0]["question_block_count"], 2)
        self.assertEqual(checked["derived"]["ranked_topics"][0]["question_block_share"], "1")

        unanswered = [{
            "turn_id": "actual",
            "speaker_id": "a7",
            "speaker_role": "analyst",
            "section": "qa",
            "text": "Good morning. Congratulations on the quarter. What is next year guidance?",
        }]
        inventory = question_inventory(unanswered, courtesy_policy="courtesy_only.v2")
        self.assertEqual(inventory["excluded_courtesy_blocks"], [])
        self.assertEqual(
            inventory["question_blocks"],
            [{"analyst_turn_ids": ["actual"], "management_answer_turn_ids": []}],
        )

    def test_historical_sol_v4_vectors_are_byte_exact(self):
        for vector in self.SOL_V4_VECTORS:
            backend = vector["backend"]
            with self.subTest(backend=backend):
                self.assertEqual(
                    model.configuration_for(
                        backend=backend,
                        prompt_version=model.COVERAGE_PROMPT_VERSION,
                        extractor_model=model.REVIEWER,
                    ),
                    vector["configuration"],
                )
                self.assertEqual(
                    model.request_identity_for(
                        self.SOURCE,
                        backend=backend,
                        prompt_version=model.COVERAGE_PROMPT_VERSION,
                        extractor_model=model.REVIEWER,
                    ),
                    vector["request_identity"],
                )
                raw = model.make_request(
                    self.SOURCE,
                    prompt_version=model.COVERAGE_PROMPT_VERSION,
                    extractor_model=model.REVIEWER,
                )
                self.assertEqual(hashlib.sha256(raw).hexdigest(), vector["request_sha256"])


class TranscriptReadinessPipelineTests(unittest.TestCase):
    setUp = fixtures.TranscriptPipelineTests.setUp
    tearDown = fixtures.TranscriptPipelineTests.tearDown

    def _rewrite_api_plan_as_v4(self):
        prepared = self.job.prepare(
            symbol="AAPL", limit=1, review_pilot=False, max_usd="3"
        )
        path = self.job.root / "plans" / (prepared["plan_id"] + ".json")
        plan = json.loads(path.read_text())
        version = model.COVERAGE_PROMPT_VERSION
        plan["extractor_configuration"] = model.configuration_for(prompt_version=version)
        plan["reserved_upper_usd"] = str(model.reservation_usd(prompt_version=version))
        identifier = fixtures.digest(plan)
        atomic(self.job.root / "plans" / (identifier + ".json"), plan)
        return identifier

    def test_default_paired_api_plan_has_sufficient_v5_budget(self):
        plan = self.job.prepare(
            symbol="AAPL", limit=1, review_pilot=True, max_usd="3"
        )
        self.assertEqual(plan["extractor_configuration"]["max_output_tokens"], 32768)
        self.assertEqual(plan["reviewer_configuration"]["max_output_tokens"], 32768)
        self.assertGreater(float(plan["reserved_upper_usd"]), 2.0)
        self.assertLessEqual(float(plan["reserved_upper_usd"]), 3.0)

    def test_old_api_plan_uses_recorded_v4_budget_and_replays_without_a_call(self):
        identifier = self._rewrite_api_plan_as_v4()
        transport = fixtures.Transport([response_with_usage(self.output, 500)])
        result = self.job.execute(identifier, transport)
        supplied = json.loads(transport.calls[0]["input"][0]["content"][0]["text"])
        self.assertEqual(transport.calls[0]["max_output_tokens"], 16384)
        self.assertNotIn("reference_index", supplied)
        self.assertEqual(result["requests_this_run"], 1)
        replay = fixtures.Transport([])
        again = self.job.execute(identifier, replay)
        self.assertEqual(again["requests_this_run"], 0)
        self.assertEqual(replay.calls, [])

    def test_v5_accepts_observed_19884_token_output(self):
        plan = self.job.prepare(
            symbol="AAPL", limit=1, review_pilot=False, max_usd="3"
        )
        result = self.job.execute(
            plan["plan_id"], fixtures.Transport([response_with_usage(self.output, 19884)])
        )
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual(result["results"][0]["analysis_outcome"], "published")

    def test_v5_rejects_32769_and_replay_makes_no_call_or_canonical_change(self):
        plan = self.job.prepare(
            symbol="AAPL", limit=1, review_pilot=False, max_usd="3"
        )
        before = mutation_fingerprint(self.stores)
        transport = fixtures.Transport([response_with_usage(self.output, 32769)])
        with self.assertRaises(ResourceLimitError):
            self.job.execute(plan["plan_id"], transport)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(before, mutation_fingerprint(self.stores))
        replay = fixtures.Transport([])
        with self.assertRaises(ResourceLimitError):
            self.job.execute(plan["plan_id"], replay)
        self.assertEqual(replay.calls, [])
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_legacy_v4_still_rejects_observed_19884_without_retry_or_publish(self):
        identifier = self._rewrite_api_plan_as_v4()
        before = mutation_fingerprint(self.stores)
        transport = fixtures.Transport([response_with_usage(self.output, 19884)])
        with self.assertRaises(ResourceLimitError):
            self.job.execute(identifier, transport)
        self.assertEqual(len(transport.calls), 1)
        replay = fixtures.Transport([])
        with self.assertRaises(ResourceLimitError):
            self.job.execute(identifier, replay)
        self.assertEqual(replay.calls, [])
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_private_sol_v4_plan_remains_pinned_on_replay(self):
        pilot = TranscriptExtractionPilot(
            self.stores, self.registry, Path(self.temp.name) / "private-pilot",
            clock=lambda: fixtures.AT,
        )
        prepared = pilot.prepare(capture_id=self.capture)
        path = pilot.root / "plans" / (prepared["plan_id"] + ".json")
        plan = json.loads(path.read_text())
        version = model.COVERAGE_PROMPT_VERSION
        plan["configuration"] = pilot.configuration(version)
        request = model.make_request(
            self.source, extractor_model=model.REVIEWER, prompt_version=version
        )
        plan["request_sha256"] = fixtures.digest(request)
        identifier = fixtures.digest(plan)
        atomic(pilot.root / "plans" / (identifier + ".json"), plan)
        transport = PilotTransport([response_with_usage(self.output, 500, review=True)])
        result = pilot.execute(identifier, transport)
        supplied = json.loads(transport.calls[0]["input"][0]["content"][0]["text"])
        self.assertEqual(result["outcome"], "validated_private")
        self.assertEqual(transport.calls[0]["max_output_tokens"], 16384)
        self.assertNotIn("reference_index", supplied)
        replay = PilotTransport([])
        again = pilot.execute(identifier, replay)
        self.assertEqual(again["requests_this_run"], 0)
        self.assertEqual(replay.preflights, 0)
        self.assertEqual(replay.calls, [])

    def test_v5_extraction_and_review_share_courtesy_inventory_and_replay_exactly(self):
        base = json.loads(self.raw)
        courtesy_rows = [
            {"speakerName": "Analyst Three", "speakerRole": "Analyst",
             "text": "Thank you so much."},
            {"speakerName": "Analyst Four", "speakerRole": "Analyst",
             "text": "Good morning. Congratulations on the quarter."},
            {"speakerName": "Analyst Five", "speakerRole": "Analyst",
             "text": "Wonderful. Thank you so much."},
            {"speakerName": "Analyst Six", "speakerRole": "Analyst",
             "text": "Great. Appreciate the caller and congrats again."},
        ]
        rows = base["data"] + courtesy_rows
        event = {
            "id": "call_AMD_2026_1",
            "eventType": "EarningsCall",
            "fiscalYear": 2026,
            "fiscalQuarter": 1,
            "hasTranscript": True,
        }
        raw = dumps_strict({
            **base,
            "ticker": "AMD",
            "eventId": event["id"],
            "turnCount": len(rows),
            "totalTurnCount": len(rows),
            "data": rows,
        }).encode()
        EquiblesTranscriptPublisher(self.stores, self.registry).publish(
            symbol="AMD",
            instrument_id="readiness-fixture-AMD",
            event=event,
            pages=(RawPage(
                raw,
                fixtures.SOURCE_AT,
                "equibles-transcripts/blobs/" + hashlib.sha256(raw).hexdigest() + ".json",
            ),),
        )
        from quant_data.stores import quiet_immutable_read_connection
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            capture_id = connection.execute(
                "SELECT capture_id FROM company_equibles_transcripts WHERE symbol='AMD'"
            ).fetchone()[0]
        source = read_source(self.stores, capture_id)
        output = deepcopy(self.output)
        output["management_tone"]["by_speaker"][0]["speaker_id"] = source["turns"][0]["speaker_id"]
        courtesy_ids = {
            turn["turn_id"] for turn in source["turns"]
            if turn["text"] in {row["text"] for row in courtesy_rows}
        }
        self.assertEqual(len(courtesy_ids), 4)
        with self.assertRaises(ValidationError):
            validate_review(self.review, output, source["turns"])

        job = type(self.job)(
            self.stores, self.registry, Path(self.temp.name) / "v5-review",
            clock=lambda: fixtures.AT,
        )
        plan = job.prepare(
            symbol="AMD", limit=1, review_pilot=True, max_usd="3"
        )
        transport = fixtures.Transport([
            response_with_usage(output, 19884),
            response_with_usage(self.review, 500, review=True),
        ])
        result = job.execute(plan["plan_id"], transport)
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual(result["requests_this_run"], 2)
        analysis_id = result["results"][0]["analysis_id"]
        stored = self.repository.get(analysis_id, as_of=fixtures.AT)
        inventory = stored["source_question_inventory"]
        self.assertEqual(inventory["courtesy_policy_version"], "courtesy_only.v2")
        self.assertEqual(
            {block[0] for block in inventory["excluded_courtesy_blocks"]},
            courtesy_ids,
        )
        self.assertEqual(len(inventory["question_blocks"]), 2)
        self.assertEqual(len(stored["reviews"]), 1)
        self.assertEqual(
            [turn["text"] for turn in read_source(self.stores, capture_id)["turns"]],
            [row["text"] for row in rows],
        )

        after = mutation_fingerprint(self.stores)
        replay = fixtures.Transport([])
        again = job.execute(plan["plan_id"], replay)
        self.assertEqual(again["requests_this_run"], 0)
        self.assertEqual(replay.calls, [])
        self.assertEqual(after, mutation_fingerprint(self.stores))


if __name__ == "__main__":
    unittest.main()
