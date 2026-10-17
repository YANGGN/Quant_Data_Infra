"""Bounded profile compatibility and private-call safety checks; synthetic stores only."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.company import transcript_structured_call as baseline
from quant_data.company import transcript_structured_call_terra_sol as profile
from quant_data.company import transcript_analysis_codex as codex
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.transcript_analysis_pair_pilot import TranscriptModelPairPilot
from . import test_transcript_analysis_pipeline as fixtures
from .test_transcript_analysis_pair import PairTransport, response, events
from .test_transcript_structured_call import example, review


class TerraSolProfileTests(unittest.TestCase):
    setUp = fixtures.TranscriptPipelineTests.setUp
    tearDown = fixtures.TranscriptPipelineTests.tearDown

    def pilot(self, clock=lambda: fixtures.AT):
        return TranscriptModelPairPilot(self.stores, self.registry,
                                        Path(self.temp.name) / "terra-sol", clock=clock)

    def test_only_models_and_efforts_differ_from_stronger_requests(self):
        for is_review in (False, True):
            draft = example(self.source) if is_review else None
            old = json.loads(baseline.make_request(self.source, analysis=draft))
            new = json.loads(profile.make_request(self.source, analysis=draft))
            self.assertEqual(new.pop("model"), profile.REVIEWER if is_review else profile.EXTRACTOR)
            self.assertEqual(new.pop("reasoning"), {"effort": "medium" if is_review else "high"})
            old.pop("model"); old.pop("reasoning")
            self.assertEqual(new, old)

    def test_explicit_selection_preserves_default_and_distinguishes_plan_identity(self):
        job = self.pilot()
        default = job.prepare(capture_id=self.capture)
        comparison = job.prepare(capture_id=self.capture, output_format="structured_terra_sol")
        self.assertEqual(default["configuration"], baseline.configuration_for())
        self.assertEqual(default["review_configuration"], baseline.configuration_for(review=True))
        self.assertEqual(comparison["configuration"], profile.configuration_for())
        self.assertEqual(comparison["review_configuration"], profile.configuration_for(review=True))
        self.assertNotEqual(default["plan_id"], comparison["plan_id"])

    def test_two_calls_are_private_and_replay_uses_no_credentials_or_calls(self):
        job = self.pilot()
        before = mutation_fingerprint(self.stores)
        plan = job.prepare(capture_id=self.capture, output_format="structured_terra_sol")
        draft = example(self.source)
        transport = PairTransport([response(draft, profile.EXTRACTOR), response(review(draft), profile.REVIEWER)])
        with patch("quant_data.company.transcript_analysis.TranscriptAnalysisPublisher.publish",
                   side_effect=AssertionError("No canonical publication")):
            result = job.execute(plan["plan_id"], transport)
        self.assertEqual(result["outcome"], "validated_private")
        self.assertEqual(result["requests_this_run"], 2)
        self.assertEqual([(r["model"], r["reasoning"]["effort"]) for r in transport.calls],
                         [(profile.EXTRACTOR, "high"), (profile.REVIEWER, "medium")])
        self.assertEqual(Path(result["brief_artifact"]).read_text(), profile.render_brief(draft))
        replay = PairTransport([])
        self.assertEqual(job.execute(plan["plan_id"], replay)["requests_this_run"], 0)
        self.assertEqual((replay.preflights, replay.calls), (0, []))
        self.assertEqual(before, mutation_fingerprint(self.stores))

    def test_native_cli_pins_each_effort_and_retains_v2_receipt(self):
        for is_review in (False, True):
            draft = example(self.source)
            root = Path(self.temp.name) / str(is_review)
            native = codex.CodexTranscriptTransport(evidence_root=root, timeout_seconds=1200)
            native._ready = True
            with patch.object(native, "_run", return_value=(events(review(draft) if is_review else draft), b"")) as run:
                output = json.loads(native.request(profile.make_request(self.source, analysis=draft if is_review else None)))
            effort = "medium" if is_review else "high"
            self.assertIn("model_reasoning_effort=" + json.dumps(effort), run.call_args.args[0])
            self.assertEqual(output["transport_evidence"]["transport"], codex.PAIR_TRANSPORT_VERSION)
            started = json.loads(next(root.glob("*/started.json")).read_text())
            self.assertEqual((started["effort"], started["transport"]), (effort, codex.PAIR_TRANSPORT_VERSION))

    def test_role_effort_prompt_schema_and_format_mutations_reject_before_execution(self):
        draft = example(self.source)
        requests = [
            (False, lambda r: r.update(model=profile.REVIEWER)),
            (True, lambda r: r.update(model=profile.EXTRACTOR, reasoning={"effort": "high"})),
            (True, lambda r: r.update(reasoning={"effort": "high"})),
            (True, lambda r: r.update(instructions="Generic review")),
            (False, lambda r: r["text"]["format"].update(name="other")),
            (False, lambda r: r["text"]["format"].update(schema={})),
        ]
        native = codex.CodexTranscriptTransport(evidence_root=Path(self.temp.name)/"reject")
        native._ready = True
        for is_review, mutate in requests:
            value = json.loads(profile.make_request(self.source, analysis=draft if is_review else None))
            mutate(value)
            with self.subTest(value=value), patch.object(native, "_run", side_effect=AssertionError("No run")):
                with self.assertRaises(ValidationError):
                    native.request(dumps_strict(value).encode())

    def test_uncertain_review_cannot_be_retried_through_a_new_plan(self):
        job = self.pilot()
        plan = job.prepare(capture_id=self.capture, output_format="structured_terra_sol")
        with self.assertRaises(ResourceLimitError):
            job.execute(plan["plan_id"], PairTransport([response(example(self.source), profile.EXTRACTOR),
                                                       ResourceLimitError("Fixture timeout")]))
        later = self.pilot(clock=lambda: fixtures.LATER)
        plan = later.prepare(capture_id=self.capture, output_format="structured_terra_sol")
        retry = PairTransport([])
        with self.assertRaises(ConflictError):
            later.execute(plan["plan_id"], retry)
        self.assertEqual((retry.preflights, retry.calls), (0, []))
