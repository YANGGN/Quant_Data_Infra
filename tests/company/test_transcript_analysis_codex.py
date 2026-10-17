from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.company import transcript_analysis_codex as codex
from quant_data.company.transcript_analysis import _response, select_captures
from quant_data.company.transcript_analysis_model import configuration_for, request_identity_for, make_request
from quant_data.errors import ValidationError, ResourceLimitError, ConflictError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
import tests.company.test_transcript_analysis_pipeline as fixtures


def events(output=None, usage=None):
    return (dumps_strict({"type":"thread.started","thread_id":"fixture"})+"\n"
        +dumps_strict({"type":"turn.started"})+"\n"
        +dumps_strict({"type":"item.completed","item":{"type":"agent_message","text":dumps_strict(output or {"ok":True})}})+"\n"
        +dumps_strict({"type":"turn.completed","usage":usage or {"input_tokens":100,"cached_input_tokens":20,"output_tokens":30,"reasoning_output_tokens":10}})+"\n").encode()


class CodexTransportTests(unittest.TestCase):
    def test_native_output_usage_and_exact_cli_bytes_are_preserved(self):
        raw=events()
        normalized=codex.decode_events(raw,"gpt-5.6-terra")
        result=json.loads(normalized)
        output,usage=_response(normalized,"gpt-5.6-terra")
        self.assertEqual(output,{"ok":True})
        self.assertEqual(usage["total_tokens"],130)
        self.assertEqual(base64.b64decode(result["transport_evidence"]["stdout_base64"]),raw)
        self.assertEqual(result["transport_evidence"]["billing_mode"],"chatgpt_subscription")

    def test_incomplete_refused_malformed_and_tool_events_are_rejected(self):
        for value in (b'{"type":"turn.failed"}',events()+b'{"type":"turn.started"}\n',
                      events()+b'{"type":"item.started","item":{"type":"command_execution"}}\n',
                      b"not json",events(usage={"input_tokens":True,"output_tokens":2})):
            with self.subTest(value=value[:40]):
                with self.assertRaises(ValidationError):
                    codex.decode_events(value,"gpt-5.6-terra")

    def test_exact_astra_metadata_warning_is_retained_without_claiming_verified_effort(self):
        warning = ("Model metadata for " + chr(96) + "gpt-6-astra" + chr(96) +
                   " not found. Defaulting to fallback metadata; this can degrade performance and cause issues.")
        diagnostic = dumps_strict({"type": "item.completed", "item": {
            "id": "item_0", "type": "error", "message": warning}}).encode() + b"\n"
        result = json.loads(codex.decode_events(
            diagnostic + events(), "gpt-6-astra", transport_version=codex.PAIR_TRANSPORT_VERSION))
        evidence = result["transport_evidence"]
        self.assertEqual(evidence["metadata_warnings"], [warning])
        self.assertEqual(evidence["model_metadata_status"], "fallback")
        self.assertFalse(evidence["effective_reasoning_effort_verified"])

    def test_other_error_items_or_wrong_model_warning_still_fail(self):
        warning = ("Model metadata for " + chr(96) + "gpt-6-astra" + chr(96) +
                   " not found. Defaulting to fallback metadata; this can degrade performance and cause issues.")
        for expected, message in (("gpt-5.6-sol", warning), ("gpt-6-astra", "Provider failed")):
            diagnostic = dumps_strict({"type": "item.completed", "item": {
                "id": "item_0", "type": "error", "message": message}}).encode() + b"\n"
            with self.subTest(expected=expected), self.assertRaises(ValidationError):
                codex.decode_events(diagnostic + events(), expected)

    def test_environment_drops_keys_custom_endpoints_and_codex_overrides(self):
        result=codex.subscription_environment({"HOME":"/home/test","PATH":"/usr/bin",
            "OPENAI_API_KEY":"secret","CODEX_API_KEY":"secret","CODEX_ACCESS_TOKEN":"secret",
            "OPENAI_BASE_URL":"https://other.invalid","CODEX_HOME":"/unexpected","EQUIBLES_API_KEY":"secret"})
        self.assertEqual(result,{"HOME":"/home/test","PATH":"/usr/bin"})

    def test_command_pins_subscription_model_sandbox_and_zero_retries(self):
        cmd=codex.cli_command(Path("/codex"),Path("/tmp/input"),"gpt-5.6-terra")
        joined=" ".join(cmd)
        for expected in ('forced_login_method="chatgpt"','model_reasoning_effort="high"',
                         "request_max_retries=0","stream_max_retries=0",
                         "--ignore-user-config","--strict-config","--sandbox read-only",
                         "--disable shell_tool","--disable multi_agent","--disable plugins"):
            self.assertIn(expected,joined)
        self.assertNotIn("danger-full-access",joined)
        self.assertNotIn("OPENAI_API_KEY",joined)

    def test_missing_or_wrong_auth_fails_before_execution_receipt(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root=Path(temp)
            transport=codex.CodexTranscriptTransport(binary=root/"missing",evidence_root=root/"evidence",environment={})
            with self.assertRaises(ValidationError): transport.prepare_credentials()
            self.assertFalse((root/"evidence").exists())
            transport=codex.CodexTranscriptTransport(binary=Path("/usr/bin/python3"),evidence_root=root/"evidence",environment={})
            with self.assertRaises(ValidationError): transport.prepare_credentials()
            self.assertFalse((root/"evidence").exists())

    def test_bounded_process_retains_events_and_rejects_reexecution(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root=Path(temp);binary=root/"fake-codex"
            binary.write_text("#!/usr/bin/python3\nimport sys\n"
                "if '--version' in sys.argv: print('codex-cli 0.144.4')\n"
                "elif 'status' in sys.argv: print('Logged in using ChatGPT')\n"
                "else: sys.stdout.buffer.write("+repr(events())+")\n")
            binary.chmod(0o700)
            transport=codex.CodexTranscriptTransport(binary=binary,evidence_root=root/"evidence",environment={"PATH":"/usr/bin","HOME":temp})
            transport.prepare_credentials()
            raw=make_request({"test":"fixture"})
            result=transport.request(raw)
            self.assertEqual(_response(result,"gpt-5.6-terra")[0],{"ok":True})
            with self.assertRaises(ValidationError): transport.request(raw)
            evidence=root/"evidence"/hashlib.sha256(raw).hexdigest()
            self.assertEqual((evidence/"stdout.jsonl").read_bytes(),events())
            self.assertEqual(json.loads((evidence/"finished.json").read_text())["exit_code"],0)

    def test_authorized_deadline_extends_only_the_selected_transport(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root=Path(temp);binary=root/"fake-codex"
            binary.write_text("#!/usr/bin/python3\nimport sys,time\ntime.sleep(0.3)\n"
                "sys.stdout.buffer.write("+repr(events())+")\n")
            binary.chmod(0o700)
            transport=codex.CodexTranscriptTransport(binary=binary,evidence_root=root/"retry",
                environment={"PATH":"/usr/bin","HOME":temp},timeout_seconds=1200)
            transport._ready=True
            raw=make_request({"test":"deadline fixture"})
            with patch.object(codex,"REQUEST_TIMEOUT_SECONDS",0.05):
                result=transport.request(raw)
                ordinary=codex.CodexTranscriptTransport(evidence_root=root/"ordinary")
                self.assertEqual(ordinary._deadline_seconds(),0.05)
            self.assertEqual(_response(result,"gpt-5.6-terra")[0],{"ok":True})
            evidence=root/"retry"/hashlib.sha256(raw).hexdigest()
            self.assertEqual(json.loads((evidence/"started.json").read_text())["timeout_seconds"],1200)
            self.assertEqual(json.loads((evidence/"started.json").read_text())["effort"],"high")
            with self.assertRaises(ValidationError): transport.request(raw)

    def test_invalid_deadline_is_rejected_before_starting_an_attempt(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            for value in (0,-1,1201,True,1200.0,"1200"):
                with self.subTest(value=value), self.assertRaises(ValidationError):
                    codex.CodexTranscriptTransport(evidence_root=Path(temp)/"evidence",timeout_seconds=value)
            self.assertFalse((Path(temp)/"evidence").exists())

    def test_timeout_kills_process_and_retains_failure(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root=Path(temp);binary=root/"fake-codex"
            binary.write_text("#!/usr/bin/python3\nimport time\ntime.sleep(60)\n")
            binary.chmod(0o700)
            transport=codex.CodexTranscriptTransport(binary=binary,evidence_root=root/"evidence",environment={"PATH":"/usr/bin","HOME":temp})
            transport._ready=True
            raw=make_request({"test":"fixture"})
            with patch.object(codex,"REQUEST_TIMEOUT_SECONDS",0.2):
                with self.assertRaises(ResourceLimitError): transport.request(raw)
            evidence=root/"evidence"/hashlib.sha256(raw).hexdigest()
            self.assertEqual(json.loads((evidence/"finished.json").read_text())["failure"],"timeout")
            with self.assertRaises(ValidationError): transport.request(raw)


    def test_selector_setup_failure_terminates_the_launched_process(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root=Path(temp);binary=root/"fake-codex"
            binary.write_text("#!/usr/bin/python3\nimport time\ntime.sleep(60)\n")
            binary.chmod(0o700)
            transport=codex.CodexTranscriptTransport(binary=binary,evidence_root=root/"evidence",
                environment={"PATH":"/usr/bin","HOME":temp})
            transport._ready=True
            launched=[]
            original=codex.subprocess.Popen
            def launch(*args,**kwargs):
                process=original(*args,**kwargs);launched.append(process);return process
            with patch.object(codex.subprocess,"Popen",side_effect=launch), patch.object(
                    codex.selectors,"DefaultSelector",side_effect=RuntimeError("selector failed")):
                with self.assertRaises(RuntimeError): transport.request(make_request({"fixture":"setup failure"}))
            self.assertEqual(len(launched),1)
            self.assertIsNotNone(launched[0].poll())

    def test_deadline_kills_descendant_after_the_leader_has_exited(self):
        import signal
        import time
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root=Path(temp);binary=root/"fake-codex";pid_path=root/"child.pid"
            binary.write_text("#!/usr/bin/python3\nimport os,time\nfrom pathlib import Path\n"
                "child=os.fork()\n"
                "if child:\n    Path("+repr(str(pid_path))+").write_text(str(child))\n    os._exit(0)\n"
                "time.sleep(60)\n")
            binary.chmod(0o700)
            transport=codex.CodexTranscriptTransport(binary=binary,evidence_root=root/"evidence",
                environment={"PATH":"/usr/bin","HOME":temp})
            transport._ready=True
            child=None
            try:
                with patch.object(codex,"REQUEST_TIMEOUT_SECONDS",0.3):
                    with self.assertRaises(ResourceLimitError):
                        transport.request(make_request({"fixture":"exited leader"}))
                child=int(pid_path.read_text())
                stat_path=Path("/proc")/str(child)/"stat"
                for _ in range(50):
                    if not stat_path.exists() or stat_path.read_text().split()[2]=="Z": break
                    time.sleep(0.01)
                self.assertTrue(not stat_path.exists() or stat_path.read_text().split()[2]=="Z")
            finally:
                if child is None and pid_path.exists(): child=int(pid_path.read_text())
                if child is not None:
                    try: os.kill(child,signal.SIGKILL)
                    except ProcessLookupError: pass


class SubscriptionPipelineTests(unittest.TestCase):
    setUp=fixtures.TranscriptPipelineTests.setUp
    tearDown=fixtures.TranscriptPipelineTests.tearDown

    def transport(self,values):
        result=fixtures.Transport(values);result.backend="codex_subscription";return result

    def plan(self,**kwargs):
        return self.job.prepare(symbol="AAPL",limit=1,review_pilot=True,backend="codex_subscription",**kwargs)

    def test_subscription_plan_is_finite_without_api_spend_or_credentials(self):
        before=mutation_fingerprint(self.stores)
        with patch("quant_data.operations.transcript_analysis.read_project_credential",side_effect=AssertionError("No API key")):
            plan=self.plan()
        self.assertEqual(plan["max_requests"],2)
        self.assertEqual(plan["contract"],"transcript_analysis_plan.v2")
        self.assertIsNone(plan["max_usd"]);self.assertIsNone(plan["reserved_upper_usd"])
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with self.assertRaises(ValidationError): self.plan(max_usd="10")

    def test_backend_is_bound_to_identity_and_mismatch_cannot_consume_a_request(self):
        self.assertNotEqual(request_identity_for(self.source),
                            request_identity_for(self.source,backend="codex_subscription"))
        plan=self.plan()
        transport=fixtures.Transport([])
        with self.assertRaises(ValidationError): self.job.execute(plan["plan_id"],transport)
        self.assertEqual(transport.calls,[])
        with self.assertRaises(ValidationError): configuration_for(backend="unknown")

    def test_subscription_extract_review_publish_and_replay_without_model_calls(self):
        plan=self.plan()
        transport=self.transport([fixtures.response(self.output),fixtures.response(self.review,review=True)])
        result=self.job.execute(plan["plan_id"],transport)
        self.assertEqual(result["requests_this_run"],2)
        self.assertIsNone(result["reserved_usd"])
        row=self.repository.find_request(request_identity_for(self.source,backend="codex_subscription"))
        self.assertEqual(json.loads(row["configuration_json"])["backend"],"codex_subscription")
        before=mutation_fingerprint(self.stores)
        result=self.job.execute(plan["plan_id"],self.transport([]))
        self.assertEqual(result["requests_this_run"],0)
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_failed_subscription_request_cannot_be_retried_by_another_plan(self):
        plan=self.plan()
        with self.assertRaises(RuntimeError):
            self.job.execute(plan["plan_id"],self.transport([RuntimeError("failure")]))
        other=self.plan()
        transport=self.transport([])
        with self.assertRaises(ConflictError): self.job.execute(other["plan_id"],transport)
        self.assertEqual(transport.calls,[])

    def test_selection_offset_is_bounded_and_does_not_repeat_the_first_capture(self):
        self.assertEqual(select_captures(self.stores,symbol="AAPL",limit=1),[self.capture])
        with self.assertRaises(ValidationError): select_captures(self.stores,symbol="AAPL",limit=1,offset=1)
        for offset in (-1,1001,True):
            with self.assertRaises(ValidationError): select_captures(self.stores,symbol="AAPL",limit=1,offset=offset)


if __name__=="__main__": unittest.main()
