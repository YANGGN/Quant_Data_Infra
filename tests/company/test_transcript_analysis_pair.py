from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.company import transcript_analysis_codex as codex
from quant_data.company import transcript_analysis_pair as pair
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.equibles_transcript_backfill import atomic
from quant_data.operations.transcript_analysis_pair_pilot import TranscriptModelPairPilot
from . import test_transcript_analysis_pipeline as fixtures


def response(output, model):
    return dumps_strict({
        "status": "completed", "model": model,
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": dumps_strict(output)}]}],
        "usage": {"input_tokens": 100, "output_tokens": 500, "total_tokens": 600},
    }).encode()


def events(output):
    return (dumps_strict({"type": "turn.started"}) + "\n"
            + dumps_strict({"type": "item.completed", "item": {
                "type": "agent_message", "text": dumps_strict(output)}}) + "\n"
            + dumps_strict({"type": "turn.completed", "usage": {
                "input_tokens": 100, "output_tokens": 500}}) + "\n").encode()


class PairTransport:
    backend = pair.BACKEND

    def __init__(self, values):
        self.values = list(values)
        self.calls = []
        self.preflights = 0

    def _deadline_seconds(self):
        return pair.REQUEST_TIMEOUT_SECONDS

    def prepare_credentials(self):
        self.preflights += 1

    def request(self, raw):
        self.calls.append(json.loads(raw))
        if not self.values:
            raise AssertionError("Unexpected model request")
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class TranscriptModelPairPilotTests(unittest.TestCase):
    setUp = fixtures.TranscriptPipelineTests.setUp
    tearDown = fixtures.TranscriptPipelineTests.tearDown

    def pilot(self, name="pair", clock=lambda: fixtures.AT):
        return TranscriptModelPairPilot(
            self.stores, self.registry, Path(self.temp.name) / name, clock=clock)

    def extraction_response(self, output=None):
        return response(self.output if output is None else output, pair.EXTRACTOR)

    def review_response(self, output=None):
        return response(self.review if output is None else output, pair.REVIEWER)

    def test_valid_pair_is_private_and_replays_with_zero_credentials_or_database_writes(self):
        job = self.pilot()
        before = mutation_fingerprint(self.stores)
        plan = job.prepare(capture_id=self.capture, output_format="detailed")
        self.assertEqual(plan["max_requests"], 2)
        self.assertEqual(plan["model_requests_made"], 0)
        self.assertTrue(plan["review_rejected_draft"])
        transport = PairTransport([self.extraction_response(), self.review_response()])
        with patch("quant_data.company.transcript_analysis.TranscriptAnalysisPublisher.publish",
                   side_effect=AssertionError("Private pair cannot publish")):
            result = job.execute(plan["plan_id"], transport)
        self.assertEqual(result["outcome"], "validated_private")
        self.assertFalse(result["canonical_published"])
        self.assertEqual(result["requests_this_run"], 2)
        self.assertEqual([(x["model"], x["reasoning"]["effort"]) for x in transport.calls],
                         [(pair.EXTRACTOR, pair.EFFORT), (pair.REVIEWER, pair.EFFORT)])
        replay = PairTransport([])
        again = job.execute(plan["plan_id"], replay)
        self.assertEqual(again["requests_this_run"], 0)
        self.assertEqual(replay.preflights, 0)
        self.assertEqual(replay.calls, [])
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_schema_shaped_semantic_failure_is_still_reviewed_but_cannot_validate(self):
        job = self.pilot("invalid-draft")
        plan = job.prepare(capture_id=self.capture, output_format="detailed")
        invalid = deepcopy(self.output)
        invalid["guidance_coverage"]["reviewed_management_turn_ids"].pop()
        transport = PairTransport([self.extraction_response(invalid), self.review_response()])
        result = job.execute(plan["plan_id"], transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(result["extraction"]["validation"], "failed")
        self.assertEqual(result["review"]["validation"], "passed")
        self.assertEqual(result["review"]["verdict"], "accepted")
        self.assertEqual(result["outcome"], "needs_changes")

    def test_output_limit_failure_is_retained_reviewed_and_never_passes(self):
        for stage in ("extraction", "review"):
            with self.subTest(stage=stage):
                job = self.pilot("output-cap-" + stage)
                plan = job.prepare(capture_id=self.capture, output_format="detailed")
                raws = [json.loads(self.extraction_response()), json.loads(self.review_response())]
                index = 0 if stage == "extraction" else 1
                raws[index]["usage"].update(output_tokens=pair.MAX_OUTPUT_TOKENS + 1,
                                           total_tokens=pair.MAX_OUTPUT_TOKENS + 101)
                transport = PairTransport([dumps_strict(value).encode() for value in raws])
                result = job.execute(plan["plan_id"], transport)
                self.assertEqual(result["requests_this_run"], 2)
                self.assertEqual(result[stage]["usage_validation"], "failed")
                self.assertEqual(result["outcome"], "needs_changes")
                self.assertFalse(result["canonical_published"])
                replay = PairTransport([])
                again = job.execute(plan["plan_id"], replay)
                self.assertEqual(again["outcome"], "needs_changes")
                self.assertEqual(again["requests_this_run"], 0)
                self.assertEqual(replay.preflights, 0)

    def test_input_usage_limit_failure_still_stops_before_review(self):
        job = self.pilot("input-cap")
        plan = job.prepare(capture_id=self.capture, output_format="detailed")
        raw = json.loads(self.extraction_response())
        from quant_data.company.transcript_analysis_model import MAX_REQUEST_BYTES
        raw["usage"].update(input_tokens=MAX_REQUEST_BYTES + 1,
                            total_tokens=MAX_REQUEST_BYTES + 501)
        transport = PairTransport([dumps_strict(raw).encode(), self.review_response()])
        with self.assertRaises(ResourceLimitError):
            job.execute(plan["plan_id"], transport)
        self.assertEqual(len(transport.calls), 1)

    def test_malformed_wrong_model_and_schema_failure_each_stop_after_one_call(self):
        cases = {
            "malformed": b"{}",
            "wrong-model": response(self.output, "gpt-5.6-terra"),
            "schema": self.extraction_response({**deepcopy(self.output),
                                                "schema_version": "transcript.analysis.v2"}),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                job = self.pilot(name)
                plan = job.prepare(capture_id=self.capture, output_format="detailed")
                transport = PairTransport([raw, self.review_response()])
                with self.assertRaises(ValidationError):
                    job.execute(plan["plan_id"], transport)
                self.assertEqual(len(transport.calls), 1)
                report = json.loads((job.root / "reports" / (plan["plan_id"] + ".json")).read_text())
                self.assertEqual(report["outcome"], "blocked")

    def test_failed_reviewer_is_uncertain_and_cannot_retry_from_a_new_plan(self):
        job = self.pilot("uncertain")
        first = job.prepare(capture_id=self.capture, output_format="detailed")
        with self.assertRaises(ResourceLimitError):
            job.execute(first["plan_id"], PairTransport([
                self.extraction_response(), ResourceLimitError("fixture timeout")]))
        later = self.pilot("uncertain", clock=lambda: fixtures.LATER)
        second = later.prepare(capture_id=self.capture, output_format="detailed")
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        retry = PairTransport([])
        with self.assertRaises(ConflictError):
            later.execute(second["plan_id"], retry)
        self.assertEqual(retry.preflights, 0)
        self.assertEqual(retry.calls, [])

    def test_plan_transport_deadline_and_response_tampering_fail_before_calls(self):
        job = self.pilot("guards")
        prepared = job.prepare(capture_id=self.capture, output_format="detailed")
        original = json.loads((job.root / "plans" / (prepared["plan_id"] + ".json")).read_text())
        for field, value in (("max_requests", 3), ("publication", "canonical"),
                             ("backend", "openai_api"), ("review_rejected_draft", False)):
            altered = deepcopy(original)
            altered[field] = value
            from quant_data.operations import transcript_analysis as operation
            identifier = operation.digest(altered)
            atomic(job.root / "plans" / (identifier + ".json"), altered)
            transport = PairTransport([])
            with self.subTest(field=field), self.assertRaises(ValidationError):
                job.execute(identifier, transport)
            self.assertEqual(transport.preflights, 0)
            self.assertEqual(transport.calls, [])

        for field, value in (("backend", "openai_api"), ("deadline", 1199)):
            transport = PairTransport([])
            if field == "backend":
                transport.backend = value
            else:
                transport._deadline_seconds = lambda: value
            with self.subTest(transport=field), self.assertRaises(ValidationError):
                job.execute(prepared["plan_id"], transport)
            self.assertEqual(transport.preflights, 0)

        result = job.execute(prepared["plan_id"], PairTransport([
            self.extraction_response(), self.review_response()]))
        extraction_id = result["extraction"]["request_identity"]
        path = job.root / "responses" / (extraction_id + ".json")
        atomic(path, path.read_bytes() + b" ", replace=True)
        replay = PairTransport([])
        with self.assertRaises(ConflictError):
            job.execute(prepared["plan_id"], replay)
        self.assertEqual(replay.preflights, 0)
        self.assertEqual(replay.calls, [])

    def test_review_request_and_receipt_bind_the_exact_parent_response(self):
        job = self.pilot("parent")
        plan = job.prepare(capture_id=self.capture, output_format="detailed")
        raw_parent = self.extraction_response()
        result = job.execute(plan["plan_id"], PairTransport([raw_parent, self.review_response()]))
        expected_parent = hashlib.sha256(raw_parent).hexdigest()
        review_request_id = result["review"]["request_identity"]
        receipt = json.loads((job.root / "requests" / (review_request_id + ".json")).read_text())
        review_input = json.loads((job.root / "inputs" / (review_request_id + ".json")).read_text())
        supplied = json.loads(review_input["input"][0]["content"][0]["text"])
        self.assertEqual(receipt["parent_response_sha256"], expected_parent)
        self.assertEqual(supplied["extractor_analysis"], self.output)
        self.assertNotIn("terra_analysis", supplied)

    def test_review_rejects_bad_output_pointers_claim_links_and_evidence(self):
        finding = {
            "severity": "error", "category": "guidance",
            "output_pointer": "/guidance_claims/0/metric",
            "description": "Fixture defect.",
            "evidence": [{"turn_id": "t1", "evidence_summary": "Fixture evidence."}],
            "proposed_correction": None, "finding_type": "interpretation_error",
            "related_claim_local_ids": ["g1"], "missing_information": None,
        }
        valid = {"schema_version": "transcript.review.v3", "verdict": "needs_changes",
                 "summary": "Fixture review.", "findings": [finding]}
        pair.validate_draft_review(valid, self.output, self.source["turns"])
        variants = []
        bad = deepcopy(valid); bad["findings"][0]["output_pointer"] = "/guidance_claims/99"
        variants.append(bad)
        bad = deepcopy(valid); bad["findings"][0]["related_claim_local_ids"] = ["missing"]
        variants.append(bad)
        bad = deepcopy(valid); bad["findings"][0]["evidence"][0]["turn_id"] = "missing"
        variants.append(bad)
        for review in variants:
            with self.subTest(review=review), self.assertRaises(ValidationError):
                pair.validate_draft_review(review, self.output, self.source["turns"])

    def test_requests_pin_model_effort_schema_role_and_generic_parent_key(self):
        extraction = json.loads(pair.make_request(self.source))
        review = json.loads(pair.make_request(self.source, analysis=self.output))
        self.assertEqual((extraction["model"], extraction["reasoning"]),
                         (pair.EXTRACTOR, {"effort": pair.EFFORT}))
        self.assertEqual((review["model"], review["reasoning"]),
                         (pair.REVIEWER, {"effort": pair.EFFORT}))
        self.assertEqual(extraction["text"]["format"]["name"], "transcript_analysis")
        self.assertEqual(review["text"]["format"]["name"], "transcript_review")
        supplied = json.loads(review["input"][0]["content"][0]["text"])
        self.assertEqual(supplied["extractor_analysis"], self.output)
        self.assertNotIn("terra_analysis", supplied)


class PairCodexTransportTests(unittest.TestCase):
    setUp = fixtures.TranscriptPipelineTests.setUp
    tearDown = fixtures.TranscriptPipelineTests.tearDown

    def test_native_run_pins_xhigh_and_retains_v2_effort_receipt(self):
        raw = pair.make_request(self.source)
        transport = codex.CodexTranscriptTransport(
            evidence_root=Path(self.temp.name) / "native", timeout_seconds=1200)
        transport._ready = True
        with patch.object(transport, "_run", return_value=(events(self.output), b"")) as run:
            normalized = json.loads(transport.request(raw))
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], pair.EXTRACTOR)
        self.assertIn('model_reasoning_effort="xhigh"', command)
        evidence = Path(self.temp.name) / "native" / hashlib.sha256(raw).hexdigest()
        started = json.loads((evidence / "started.json").read_text())
        self.assertEqual((started["transport"], started["effort"]),
                         (codex.PAIR_TRANSPORT_VERSION, pair.EFFORT))
        self.assertEqual(normalized["transport_evidence"]["transport"],
                         codex.PAIR_TRANSPORT_VERSION)

    def test_new_model_roles_cannot_swap_schemas(self):
        extraction = json.loads(pair.make_request(self.source))
        review = json.loads(pair.make_request(self.source, analysis=self.output))
        variants = []
        bad = deepcopy(extraction); bad["model"] = pair.REVIEWER
        variants.append(bad)
        bad = deepcopy(review); bad["model"] = pair.EXTRACTOR
        variants.append(bad)
        for index, request in enumerate(variants):
            transport = codex.CodexTranscriptTransport(
                evidence_root=Path(self.temp.name) / ("role-" + str(index)), timeout_seconds=1200)
            transport._ready = True
            with self.subTest(index=index), self.assertRaises(ValidationError):
                transport.request(dumps_strict(request).encode())
            self.assertFalse((Path(self.temp.name) / ("role-" + str(index))).exists())


if __name__ == "__main__":
    unittest.main()


class PrivateReadinessTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from quant_data.registry import load_registry, sharadar_direct_registry_profile
        from quant_data.migrations import initialize_all
        from quant_data.stores import StoreMap
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.temp.name)
        self.stores = StoreMap.four_explicit(**{
            role: root / (role + ".sqlite") for role in ("market", "macro", "company", "news")})
        project = Path(__file__).resolve().parents[2]
        self.registry = load_registry(project / "config/system_registry.json",
                                      project_root=project, environment={})
        self.predecessor = sharadar_direct_registry_profile(self.registry)
        initialize_all(self.stores, self.predecessor)

    def tearDown(self):
        self.temp.cleanup()

    def test_pending_unrelated_migration_is_private_ready_but_canonical_still_blocks(self):
        from quant_data.operations import transcript_analysis as operation
        from quant_data.operations.transcript_analysis_pair_pilot import ready_for_private_evaluation
        self.assertEqual(operation._migration_rows(self.predecessor)[-1][2], 16)
        self.assertGreater(len(operation._migration_rows(self.registry)),
                           len(operation._migration_rows(self.predecessor)))
        before = mutation_fingerprint(self.stores)
        ready_for_private_evaluation(self.stores, self.registry)
        with self.assertRaises(ValidationError):
            operation.ready(self.stores, self.registry)
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_changed_or_missing_installed_migration_never_passes(self):
        from quant_data.operations import transcript_analysis as operation
        from quant_data.operations.transcript_analysis_pair_pilot import ready_for_private_evaluation
        actual = operation._migration_rows(self.predecessor)
        changed = list(actual)
        last = list(changed[-1]); last[4] = "0" * 64; changed[-1] = tuple(last)
        variants = [changed, actual[1:], actual[:10], actual + [actual[-1]], []]
        for rows in variants:
            with self.subTest(rows=len(rows)), patch.object(operation, "_stored_migrations", return_value=rows):
                with self.assertRaises(ValidationError):
                    ready_for_private_evaluation(self.stores, self.registry)
