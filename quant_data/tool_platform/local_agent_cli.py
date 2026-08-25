"""Local strict-JSON bridge for agents in other projects.

The bridge is deliberately a process-local command, not a service. It
constructs the same host-owned Stage1Application used by the existing tool
boundary, with the canonical registry and four store paths resolved from this
source tree. Command-line callers cannot substitute a registry, project root,
store path, SQL fragment, or credential.

Only the call command reads stdin. It consumes exactly one strict JSON tool
envelope and writes one strict JSON response to stdout. The other commands are
discovery conveniences over the dispatcher manifest.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from quant_data.boundary import Stage1Application
from quant_data.boundary.dispatcher import (
    UnknownToolError,
    UnsupportedToolVersionError,
)
from quant_data.errors import Issue, QuantDataError, ResourceLimitError, ValidationError
from quant_data.json_codec import MAX_JSON_BYTES, dumps_strict
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stores import resolve_store_map


# .../quant_data/tool_platform/local_agent_cli.py -> project root. This remains
# independent of the caller's working directory and ambient variables.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_API_VERSION_FALLBACK = "1.0"


def create_local_application() -> Stage1Application:
    """Build the fixed local host without consulting ambient environment.

    Construction opens no database. Registered operations independently open
    only their host-selected read connections when a call requires one.
    """

    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    store_map = resolve_store_map(
        registry,
        project_root=PROJECT_ROOT,
        environment={},
    )
    return Stage1Application(store_map, registry)


def _validated_command(argv: Sequence[str]) -> tuple[str, str | None, str | None]:
    """Return (command, tool_name, selected_version) with no host options."""

    values = list(argv)
    if not values:
        raise ValidationError(
            "A command is required",
            issues=(
                Issue(
                    "/command",
                    "required",
                    "Use list, describe, manifest, or call",
                ),
            ),
        )
    command = values[0]
    if command in {"list", "manifest", "call"}:
        if len(values) != 1:
            raise ValidationError(
                "This command does not accept command-line arguments",
                issues=(
                    Issue(
                        "/command",
                        "additional_properties",
                        "Use strict JSON on stdin only with call",
                    ),
                ),
            )
        return command, None, None
    if command != "describe":
        raise ValidationError(
            "Unknown local tool command",
            issues=(
                Issue(
                    "/command",
                    "enum",
                    "Use list, describe, manifest, or call",
                ),
            ),
        )
    if len(values) < 2 or not isinstance(values[1], str) or not values[1]:
        raise ValidationError(
            "describe requires one public tool name",
            issues=(
                Issue("/tool", "required", "Provide a name returned by list"),
            ),
        )
    name = values[1]
    tail = values[2:]
    if not tail:
        return command, name, None
    if len(tail) != 2 or tail[0] != "--tool-version" or not tail[1]:
        raise ValidationError(
            "describe accepts only --tool-version VERSION",
            issues=(
                Issue(
                    "/tool_version",
                    "arguments",
                    "Use an advertised semantic version",
                ),
            ),
        )
    return command, name, tail[1]


def _read_one_request(stdin: BinaryIO | TextIO) -> bytes:
    """Read a bounded raw request; strict parsing remains in the application."""

    raw = stdin.read(MAX_JSON_BYTES + 1)
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
    elif isinstance(raw, bytes):
        payload = raw
    else:
        raise ValidationError("Standard input must provide text or bytes")
    if len(payload) > MAX_JSON_BYTES:
        raise ResourceLimitError("JSON request exceeds the supported byte limit")
    if not payload:
        raise ValidationError(
            "Request body is required",
            issues=(Issue("/", "required", "Provide one strict JSON tool envelope"),),
        )
    return payload


def _registry_revision(application: Stage1Application | None) -> str | None:
    if application is None:
        return None
    return application.dispatcher.registry.revision


def _error_payload(
    error: QuantDataError,
    *,
    application: Stage1Application | None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {}
    revision = _registry_revision(application)
    if revision is not None:
        receipt["registry_revision"] = revision
    return {
        "api_version": (
            application.api_version if application is not None else _API_VERSION_FALLBACK
        ),
        "execution": "read_only",
        "error": error.to_dict(),
        "receipt": receipt,
    }


def _internal_error_payload(application: Stage1Application | None) -> dict[str, Any]:
    return _error_payload(
        QuantDataError("Internal server error", code="internal_error"),
        application=application,
    )


def _write_json(stdout: TextIO, payload: Mapping[str, Any]) -> None:
    # The newline is a transport delimiter, not a second JSON value.
    stdout.write(dumps_strict(dict(payload)) + "\n")
    stdout.flush()


def _write_response_body(stdout: TextIO, body: bytes) -> None:
    # Application responses are already strict JSON. Decode only at the final
    # text boundary; invalid output is treated as a trusted-host failure.
    stdout.write(body.decode("utf-8", errors="strict") + "\n")
    stdout.flush()


def _exit_code_for_status(status: int) -> int:
    if 200 <= status < 300:
        return 0
    if 400 <= status < 500:
        return 2
    return 1


def _compact_tool(tool: Mapping[str, Any]) -> dict[str, Any]:
    versions = tool.get("versions")
    available_versions = (
        [item["version"] for item in versions if isinstance(item, Mapping)]
        if isinstance(versions, list)
        else [tool["version"]]
    )
    compatibility = tool.get("compatibility")
    return {
        "name": tool["name"],
        "version": tool["version"],
        "available_versions": available_versions,
        "lifecycle": tool["lifecycle"],
        "compatibility_status": (
            compatibility.get("status") if isinstance(compatibility, Mapping) else None
        ),
        "description": tool["description"],
        "read_only": tool["read_only"],
    }


def _find_tool(
    manifest: Mapping[str, Any],
    name: str,
    selected_version: str | None,
) -> Mapping[str, Any]:
    tools = manifest.get("tools")
    if not isinstance(tools, list):
        raise QuantDataError("Internal server error", code="internal_error")
    public = next(
        (
            item
            for item in tools
            if isinstance(item, Mapping) and item.get("name") == name
        ),
        None,
    )
    if public is None:
        raise UnknownToolError("Unknown tool")
    if selected_version is None or selected_version == public.get("version"):
        return public
    variants = public.get("versions")
    if isinstance(variants, list):
        for item in variants:
            if isinstance(item, Mapping) and item.get("version") == selected_version:
                return item
    raise UnsupportedToolVersionError("Unsupported tool version")


def _list_payload(application: Stage1Application) -> dict[str, Any]:
    manifest = application.dispatcher.manifest()
    tools = manifest["tools"]
    return {
        "api_version": manifest["api_version"],
        "execution": "read_only",
        "milestone": copy.deepcopy(manifest["milestone"]),
        "registry_revision": manifest["registry_revision"],
        "tool_count": len(tools),
        "tools": [_compact_tool(item) for item in tools],
        "receipt": {
            "registry_revision": manifest["registry_revision"],
            "command": "list",
        },
    }


def _describe_payload(
    application: Stage1Application,
    name: str,
    selected_version: str | None,
) -> dict[str, Any]:
    manifest = application.dispatcher.manifest()
    selected = _find_tool(manifest, name, selected_version)
    return {
        "api_version": manifest["api_version"],
        "execution": "read_only",
        "tool": copy.deepcopy(dict(selected)),
        "selection": {
            "name": name,
            "tool_version": selected["version"],
        },
        "receipt": {
            "registry_revision": manifest["registry_revision"],
            "command": "describe",
        },
    }


def run(
    argv: Sequence[str] | None = None,
    *,
    stdin: BinaryIO | TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    application: Stage1Application | None = None,
) -> int:
    """Run one command and write exactly one JSON document to stdout.

    application is an in-process test seam, not a command-line setting.
    Production callers always use create_local_application, whose root,
    registry, and store routing are fixed by this project.
    """

    del stderr  # Diagnostics deliberately stay out of the strict CLI surface.
    selected_stdout = stdout if stdout is not None else sys.stdout
    selected_stdin = stdin if stdin is not None else sys.stdin.buffer
    active_application = application
    try:
        command, name, selected_version = _validated_command(
            list(sys.argv[1:] if argv is None else argv)
        )
        if active_application is None:
            active_application = create_local_application()
        if command == "call":
            body = _read_one_request(selected_stdin)
            response = active_application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={},
                body=body,
            )
            _write_response_body(selected_stdout, response.body)
            return _exit_code_for_status(int(response.status))
        if command == "manifest":
            response = active_application.handle(
                "GET",
                "/api/agent-tools",
                headers={},
            )
            _write_response_body(selected_stdout, response.body)
            return _exit_code_for_status(int(response.status))
        if command == "list":
            _write_json(selected_stdout, _list_payload(active_application))
            return 0
        if command == "describe" and name is not None:
            _write_json(
                selected_stdout,
                _describe_payload(active_application, name, selected_version),
            )
            return 0
        raise QuantDataError("Internal server error", code="internal_error")
    except QuantDataError as error:
        _write_json(selected_stdout, _error_payload(error, application=active_application))
        return _exit_code_for_status(error.http_status)
    except Exception:
        _write_json(selected_stdout, _internal_error_payload(active_application))
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""

    return run(argv)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())


__all__ = (
    "PROJECT_ROOT",
    "create_local_application",
    "main",
    "run",
)
