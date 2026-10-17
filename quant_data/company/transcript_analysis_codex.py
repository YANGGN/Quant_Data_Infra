"""Subscription-only Codex CLI adapter for bounded transcript analysis."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time

from ..errors import ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from .transcript_analysis_model import (
    EXTRACTOR, REVIEWER, EFFORT, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
)

CODEX_BINARY = Path("/home/volatility/.codex/packages/standalone/releases/0.144.4-aarch64-unknown-linux-musl/codex")
CODEX_VERSION = "0.144.4"
BACKEND = "codex_subscription"
TRANSPORT_VERSION = "codex_exec.v1"
PAIR_TRANSPORT_VERSION = "codex_exec.v2"
MODEL_EFFORTS = ((EXTRACTOR, EFFORT), (REVIEWER, EFFORT),
                 ("gpt-5.6-sol", "xhigh"), ("gpt-6-astra", "xhigh"), ("gpt-5.6-sol", "medium"),
                 ("gpt-5.6-luna", "high"), ("gpt-6-astra", "high"))
MAX_STREAM_BYTES = 3 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 20 * 60
DISABLED_FEATURES = (
    "apps", "plugins", "hooks", "shell_tool", "unified_exec", "multi_agent",
    "computer_use", "browser_use", "browser_use_external", "in_app_browser",
    "image_generation", "memories", "goals", "tool_suggest", "code_mode_host",
    "workspace_dependencies", "shell_snapshot", "standalone_web_search",
    "skill_mcp_dependency_install",
)


def subscription_environment(environment):
    # The worker receives no project/provider keys, endpoint overrides or API fallback.
    allowed = ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME",
               "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
    return {k: environment[k] for k in allowed if k in environment}


def cli_command(binary, directory, model, *, effort=EFFORT):
    if (model, effort) not in MODEL_EFFORTS:
        raise ValidationError("Unsupported transcript model and reasoning effort")
    command = [str(binary), "exec", "--ignore-user-config", "--strict-config",
        "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--json", "--color", "never", "--model", model,
        "--output-schema", str(directory/"schema.json"),
        "--config", 'forced_login_method="chatgpt"',
        "--config", 'approval_policy="never"',
        "--config", 'model_reasoning_effort='+json.dumps(effort),
        "--config", 'model_reasoning_summary="none"',
        "--config", 'web_search="disabled"',
        "--config", 'mcp_servers={}',
        "--config", 'project_doc_max_bytes=0',
        "--config", 'history.persistence="none"',
        "--config", 'model_instructions_file='+json.dumps(str(directory/"instructions.txt")),
        "--config", 'model_provider="transcript_subscription"',
        "--config", 'model_providers.transcript_subscription={name="Transcript subscription",requires_openai_auth=true,wire_api="responses",request_max_retries=0,stream_max_retries=0,supports_websockets=false}',
    ]
    for feature in DISABLED_FEATURES:
        command.extend(("--disable", feature))
    return command + ["-"]


def decode_events(stdout, expected_model, *, transport_version=TRANSPORT_VERSION):
    """Normalize native CLI evidence without presenting it as an HTTP response."""
    messages, completed, started = [], [], 0
    metadata_warnings = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = loads_strict(line.decode("utf-8"))
        kind = event.get("type") if isinstance(event,dict) else None
        if kind in ("error", "turn.failed"):
            raise ValidationError("Codex run failed; inspect its retained private event receipt")
        if kind == "turn.started":
            started += 1
        if kind == "turn.completed":
            completed.append(event)
        if kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item", {})
            # This exact CLI diagnostic is a completed metadata warning, not a
            # model tool request. Preserve it explicitly; all other errors/items
            # still fail closed. It does not prove effective model capabilities.
            metadata_warning = ("Model metadata for "+chr(96)+expected_model+chr(96)+
                " not found. Defaulting to fallback metadata; this can degrade performance and cause issues.")
            if (kind == "item.completed" and expected_model == "gpt-6-astra"
                    and set(item) == {"id", "type", "message"} and item.get("type") == "error"
                    and item.get("message") == metadata_warning):
                metadata_warnings.append(metadata_warning)
                continue
            if item.get("type") not in ("agent_message", "reasoning"):
                raise ValidationError("Codex attempted an unexpected tool or item")
            if kind == "item.completed" and item.get("type") == "agent_message":
                messages.append(item.get("text"))
    if started != 1 or len(completed) != 1 or len(messages) != 1 or not isinstance(messages[0],str):
        raise ValidationError("Codex must complete one turn with one structured message")
    loads_strict(messages[0])
    native_usage=completed[0].get("usage",{})
    if any(type(native_usage.get(k)) is not int or native_usage[k]<0 for k in ("input_tokens","output_tokens")):
        raise ValidationError("Codex usage is missing or invalid")
    usage={**native_usage,"total_tokens":native_usage["input_tokens"]+native_usage["output_tokens"]}
    result={"status":"completed","model":expected_model,
        "output":[{"type":"message","role":"assistant","status":"completed",
                   "content":[{"type":"output_text","text":messages[0]}]}],
        "usage":usage,
        "transport_evidence":{"transport":transport_version,"cli_version":CODEX_VERSION,
            "billing_mode":"chatgpt_subscription","model_identity_source":"pinned_cli_configuration",
            "stdout_base64":base64.b64encode(stdout).decode("ascii")}}
    if metadata_warnings:
        result["transport_evidence"].update(model_metadata_status="fallback",
            metadata_warnings=metadata_warnings, effective_reasoning_effort_verified=False)
    raw=dumps_strict(result).encode()
    if len(raw)>MAX_RESPONSE_BYTES:
        raise ResourceLimitError("Normalized Codex response exceeds its byte bound")
    return raw


class CodexTranscriptTransport:
    backend = BACKEND

    def __init__(self, *, binary=CODEX_BINARY, evidence_root, environment=None, timeout_seconds=None):
        if timeout_seconds is not None and (
                type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS):
            raise ValidationError("Codex deadline must be an integer from 1 to 1200 seconds")
        self.timeout_seconds=timeout_seconds
        self.binary=Path(binary)
        self.evidence_root=Path(evidence_root)
        self.environment=subscription_environment(os.environ if environment is None else environment)
        self._ready=False

    def prepare_credentials(self):
        if self._ready:
            return
        if not self.binary.is_file() or not os.access(self.binary,os.X_OK):
            raise ValidationError("Native Codex CLI is unavailable")
        for args, expected in ((["--version"],"codex-cli "+CODEX_VERSION),
            (["-c",'forced_login_method="chatgpt"',"login","status"],"Logged in using ChatGPT")):
            try:
                result=subprocess.run([str(self.binary),*args],cwd="/tmp",env=self.environment,
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
            except (OSError,subprocess.TimeoutExpired):
                raise ValidationError("Codex authentication preflight failed") from None
            if result.returncode or expected not in (result.stdout+result.stderr).decode("utf-8",errors="replace"):
                raise ValidationError("Codex requires the pinned CLI and a ChatGPT subscription login")
        self._ready=True

    def request(self, raw):
        if not self._ready:
            raise ValidationError("Codex subscription was not preflighted")
        if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_REQUEST_BYTES:
            raise ResourceLimitError("Model request exceeds bound")
        request=loads_strict(raw.decode("utf-8"))
        reasoning=request.get("reasoning")
        if (not isinstance(reasoning,dict) or set(reasoning)!={"effort"}
                or (request.get("model"),reasoning.get("effort")) not in MODEL_EFFORTS
                or request.get("tools")!=[] or request.get("tool_choice")!="none"):
            raise ValidationError("Codex request differs from the fixed model contract")
        from . import transcript_analysis_pair as pair
        from . import transcript_call_brief as brief
        from . import transcript_structured_call as structured
        from . import transcript_structured_call_terra_sol as terra_sol
        from . import transcript_structured_quality as tiered
        from . import transcript_structured_call_luna_astra as luna_astra
        format_name = request.get("text", {}).get("format", {}).get("name")
        fixed_profile = False
        if (reasoning["effort"] in {"xhigh", "medium"}
                or request.get("model") in {luna_astra.EXTRACTOR, luna_astra.REVIEWER}
                or format_name in brief.FORMAT_NAMES + structured.FORMAT_NAMES + tiered.FORMAT_NAMES
                or request.get("instructions") in tuple(
                    p.request_contract(review=review)[0] for p in (structured, tiered) for review in (False, True))):
            # High-effort legacy requests retain their original lane. New fixed
            # profiles bind model, effort, role, prompt, schema and output bound.
            for profile, names in ((pair, ("transcript_analysis", "transcript_review")),
                                   (brief, brief.FORMAT_NAMES), (structured, structured.FORMAT_NAMES),
                                   (terra_sol, terra_sol.FORMAT_NAMES), (tiered, tiered.FORMAT_NAMES),
                                   (luna_astra, luna_astra.FORMAT_NAMES)):
                for review in (False, True):
                    config = profile.configuration_for(review=review)
                    if (request["model"] != config["model"]
                            or reasoning["effort"] != config["reasoning_effort"]):
                        continue
                    prompt, schema = profile.request_contract(review=review)
                    expected_format = {"type": "json_schema", "name": names[int(review)],
                                       "strict": True, "schema": schema}
                    if (request.get("text") == {"format": expected_format}
                            and request.get("max_output_tokens") == profile.MAX_OUTPUT_TOKENS
                            and request.get("instructions") == prompt):
                        fixed_profile = True
                        break
                if fixed_profile:
                    break
            if not fixed_profile:
                raise ValidationError("Upgraded request differs from its fixed extraction or review role")
        from ..operations.equibles_transcript_backfill import atomic, private_directory
        import hashlib
        identifier=hashlib.sha256(raw).hexdigest()
        private_directory(self.evidence_root)
        run_root=self.evidence_root/identifier
        private_directory(run_root)
        if (run_root/"started.json").exists():
            raise ValidationError("A Codex run already started for this request; no automatic retry")
        with tempfile.TemporaryDirectory(prefix="transcript-codex-",dir="/tmp") as temporary:
            directory=Path(temporary)
            atomic(directory/"schema.json",request["text"]["format"]["schema"])
            atomic(directory/"instructions.txt",request["instructions"].encode())
            prompt=request["input"][0]["content"][0]["text"].encode()
            atomic(directory/"prompt.json",prompt)
            effort=reasoning["effort"]
            version=PAIR_TRANSPORT_VERSION if fixed_profile else TRANSPORT_VERSION
            command=cli_command(self.binary,directory,request["model"],effort=effort)
            atomic(run_root/"started.json",{"transport":version,"cli_version":CODEX_VERSION,
                "model":request["model"],"effort":effort,"billing_mode":"chatgpt_subscription",
                "request_sha256":identifier,"timeout_seconds":self._deadline_seconds()})
            stdout,stderr=self._run(command,directory,prompt,run_root)
            return decode_events(stdout,request["model"],transport_version=version)

    def _deadline_seconds(self):
        # Explicit operational authorization can extend one attempt without
        # changing model configuration, request identity or the default deadline.
        return REQUEST_TIMEOUT_SECONDS if self.timeout_seconds is None else self.timeout_seconds

    def _run(self, command, directory, prompt, run_root):
        from ..operations.equibles_transcript_backfill import atomic
        deadline_seconds=self._deadline_seconds()
        output={"stdout":bytearray(),"stderr":bytearray()}
        reason=None
        process, selector = None, None
        started=time.monotonic()
        try:
            with (directory/"prompt.json").open("rb") as source:
                process=subprocess.Popen(command,stdin=source,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    cwd=directory,env=self.environment,start_new_session=True)
            selector=selectors.DefaultSelector()
            for name,stream in (("stdout",process.stdout),("stderr",process.stderr)):
                os.set_blocking(stream.fileno(),False)
                selector.register(stream,selectors.EVENT_READ,name)
            while selector.get_map():
                if time.monotonic()-started>=deadline_seconds:
                    reason="timeout";break
                for key,_ in selector.select(timeout=0.2):
                    chunk=os.read(key.fileobj.fileno(),65536)
                    if not chunk:
                        selector.unregister(key.fileobj);continue
                    output[key.data].extend(chunk)
                    if sum(len(v) for v in output.values())>MAX_STREAM_BYTES:
                        reason="response_bound";break
                if reason: break
            if reason is None:
                try: process.wait(timeout=max(0.01,deadline_seconds-(time.monotonic()-started)))
                except subprocess.TimeoutExpired: reason="timeout"
        except BaseException:
            reason=reason or "local_execution_failure"
            raise
        finally:
            # Descendants can outlive an already-exited leader. Always terminate
            # the whole session's process group, including after setup failures.
            try:
                if process is not None:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    finally: process.wait()
            finally:
                if selector is not None: selector.close()
                if process is not None:
                    for stream in (process.stdout,process.stderr):
                        if stream is not None: stream.close()
                atomic(run_root/"stdout.jsonl",bytes(output["stdout"]))
                atomic(run_root/"stderr.txt",bytes(output["stderr"]))
                atomic(run_root/"finished.json",{"exit_code":process.returncode if process else None,"failure":reason,
                    "elapsed_seconds":round(time.monotonic()-started,3)})
        if reason:
            raise ResourceLimitError("Codex run reached its "+reason+" bound; no automatic retry")
        if process.returncode:
            raise ValidationError("Codex execution failed; private evidence retained")
        return bytes(output["stdout"]),bytes(output["stderr"])
