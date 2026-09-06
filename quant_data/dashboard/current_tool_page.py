"""Escaped current-registry tool explorer for the Canonical Inspector.

The frozen Stage 6 portal intentionally remains on its historical registry
projection. This renderer belongs to the current Canonical Inspector and uses
only public manifest fields plus registry dataset declarations. It does not
expose handler identifiers, store paths, relation names, credentials, or
write controls.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any

from ..json_codec import dumps_strict
from ..registry import Registry
from .inspector_shell import render_inspector_shell


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _sequence(value: object) -> tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return tuple(value)
    return ()


def _tools(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    value = manifest.get("tools")
    return tuple(item for item in _sequence(value) if isinstance(item, Mapping))


def _variants(tool: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    variants = tuple(
        item
        for item in _sequence(tool.get("versions"))
        if isinstance(item, Mapping)
    )
    return variants or (tool,)


def _semantic_version_key(variant: Mapping[str, Any]) -> tuple[int, int, int]:
    version = variant.get("version")
    if not isinstance(version, str):
        return (-1, -1, -1)
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return (-1, -1, -1)
    major, minor, patch = parts
    return (int(major), int(minor), int(patch))


def _latest_variant(tool: Mapping[str, Any]) -> Mapping[str, Any]:
    return max(_variants(tool), key=_semantic_version_key)


def latest_tool_version(tool: Mapping[str, Any]) -> str:
    """Return the newest advertised semantic version for one logical tool."""

    version = _latest_variant(tool).get("version")
    return version if isinstance(version, str) else ""


def _selected_tool(
    tools: tuple[Mapping[str, Any], ...],
    requested_tool: str | None,
) -> Mapping[str, Any]:
    if requested_tool:
        for tool in tools:
            if tool.get("name") == requested_tool:
                return tool
    for preferred in ("market.get_available_ticker", "macro.search_series"):
        for tool in tools:
            if tool.get("name") == preferred:
                return tool
    return tools[0] if tools else {}


def _first_example(variant: Mapping[str, Any]) -> Mapping[str, Any]:
    for item in _sequence(variant.get("examples")):
        if isinstance(item, Mapping):
            return item
    return {}


def _availability(tool: Mapping[str, Any]) -> str:
    capability = tool.get("live_capability")
    if (
        isinstance(capability, Mapping)
        and capability.get("possible") is True
        and capability.get("offline_status") == "disabled"
    ):
        return "capability unavailable"
    policy = tool.get("availability_policy")
    if isinstance(policy, Mapping):
        status = policy.get("point_in_time_default")
        if isinstance(status, str) and status:
            return status.replace("_", " ")
    return "declared"


def _tool_inventory(tools: tuple[Mapping[str, Any], ...]) -> str:
    rows = []
    for tool in tools:
        latest = _latest_variant(tool)
        version = latest_tool_version(tool)
        name = str(tool.get("name", ""))
        link = "/agent-tools?tool=" + html.escape(name, quote=True)
        rows.append(
            '<tr><th scope="row"><a href="'
            + link
            + '"><code>'
            + _escape(name)
            + "</code></a></th><td>"
            + _escape(tool.get("family", "—"))
            + "</td><td>"
            + _escape(version)
            + "</td><td>"
            + _escape(_availability(latest))
            + "</td></tr>"
        )
    return "".join(rows)


def _dataset_inventory(registry: Registry, public_names: set[str]) -> str:
    rows: list[str] = []
    for dataset in registry.datasets:
        tool_names = tuple(
            tool_id for tool_id in dataset.tool_ids if tool_id in public_names
        )
        access = ", ".join(tool_names)
        if not access and dataset.dashboard_ids:
            access = "Fixed Inspector view"
        if not access:
            access = "No public row surface"
        rows.append(
            '<tr><th scope="row"><code>'
            + _escape(dataset.id)
            + "</code></th><td>"
            + _escape(dataset.store)
            + "</td><td>"
            + _escape(dataset.layer)
            + "</td><td>"
            + ("Active" if dataset.active else "Inactive")
            + "</td><td>"
            + _escape(access)
            + "</td></tr>"
        )
    return "".join(rows)


def render_current_agent_tools_page(
    manifest: Mapping[str, Any],
    registry: Registry,
    *,
    requested_tool: str | None = None,
) -> str:
    """Render the current manifest, generated runner hooks, and data coverage."""

    tools = _tools(manifest)
    selected_tool = _selected_tool(tools, requested_tool)
    selected_variant = _latest_variant(selected_tool)
    selected_name = str(selected_tool.get("name", ""))
    selected_version = str(selected_variant.get("version", ""))
    api_version = str(manifest.get("api_version", "1.0"))
    example = dumps_strict(_first_example(selected_variant))
    tool_options = "".join(
        '<option value="'
        + _escape(tool.get("name", ""))
        + '"'
        + (" selected" if tool.get("name") == selected_name else "")
        + ">"
        + _escape(tool.get("name", ""))
        + "</option>"
        for tool in tools
    )
    latest_versions = sum(1 for tool in tools if latest_tool_version(tool))
    public_names = {
        str(tool.get("name"))
        for tool in tools
        if isinstance(tool.get("name"), str)
    }
    manifest_rows = _tool_inventory(tools)
    dataset_rows = _dataset_inventory(registry, public_names)
    description = selected_variant.get(
        "description", selected_tool.get("description", "Read-only public tool")
    )
    lifecycle = selected_variant.get(
        "lifecycle", selected_tool.get("lifecycle", "active")
    )
    bounds = selected_variant.get("workload_bounds", {})
    body = f"""<section class="metric-grid inspector-summary" aria-label="Current manifest summary">
<article class="metric"><p>Logical tools</p><strong>{len(tools)}</strong><span>Current registered names</span></article>
<article class="metric"><p>Latest versions</p><strong>{latest_versions}</strong><span>One per logical tool</span></article>
<article class="metric"><p>Registered datasets</p><strong>{len(registry.datasets)}</strong><span>Active and retained declarations</span></article>
<article class="metric"><p>Execution</p><strong>Read only</strong><span>Loopback dispatcher boundary</span></article></section>
<p class="inspector-tool-shortcut"><a href="/agent-tools?tool=market.technical_indicators">Technical indicators</a></p>\n<section class="panel" id="tool-runner"><header class="panel-header"><p class="panel-kicker">Manifest-generated request</p>
<h2>Run a public tool</h2><p>Newest advertised version · manifest-generated fields and workload bounds.</p></header>
<form id="current-tool-runner" class="query-form tool-runner" action="/api/agent-tools/call" method="post">
<fieldset><legend class="inspector-sr-only">Read-only tool request</legend><div class="form-grid tool-selector-grid">
<label for="current-tool-name">Tool<select id="current-tool-name" name="tool" required>{tool_options}</select></label>
<label for="current-tool-version">Latest version<input id="current-tool-version" name="tool_version" value="{_escape(selected_version)}" readonly required></label>
<input type="hidden" name="api_version" value="{_escape(api_version)}"></div>
<div class="tool-contract-summary"><p data-tool-description>{_escape(description)}</p>
<p><strong>Lifecycle:</strong> <span data-tool-lifecycle>{_escape(lifecycle)}</span></p>
<p><strong>Bounds:</strong> <span data-tool-bounds>{_escape(dumps_strict(bounds))}</span></p></div>
<div class="schema-fields" data-tool-fields><p class="empty-copy">Loading controls from the local manifest…</p></div>
<details class="advanced-json"><summary>Advanced: inspect or edit strict JSON arguments</summary>
<label for="current-tool-arguments">Arguments JSON<textarea id="current-tool-arguments" name="arguments" rows="12" maxlength="8388608" required>{_escape(example)}</textarea></label></details>
<div class="saved-series" data-saved-series><p><strong>Reusable series:</strong> none returned in this browser session.</p></div>
<div class="form-actions"><button type="submit">Run read-only tool</button>
<p class="form-bound">Requests stay on this loopback server and retain the server’s workload and response bounds.</p></div></fieldset></form>
<output class="runner-output tool-result" data-tool-result data-state="idle" aria-live="polite"><strong>No tool request has run yet.</strong></output></section>
<details class="inspector-disclosure"><summary>Public tool inventory · {len(tools)} logical tools</summary><section class="panel"><header class="panel-header"><p class="panel-kicker">Public contract inventory</p><h2>{len(tools)} logical tools</h2>
<p>Only the newest advertised version of each logical tool is shown. The final column describes that version's default point-in-time policy; it is not a live-feed health status.</p></header>
<div class="inspector-table-workspace" data-inspector-table-workspace><div class="table-scroll" tabindex="0"><table data-inspector-table id="current-tool-inventory"><caption>Current public tools and latest semantic versions</caption>
<thead><tr><th>Tool</th><th>Family</th><th>Latest version</th><th>Point-in-time policy</th></tr></thead><tbody>{manifest_rows}</tbody></table></div></div></section>
</details><details class="inspector-disclosure"><summary>Registered data coverage · {len(registry.datasets)} datasets</summary><section class="panel"><header class="panel-header"><p class="panel-kicker">Registered data coverage</p><h2>{len(registry.datasets)} dataset declarations</h2>
<p>This inventory shows whether each registered dataset has a current public tool or fixed Inspector view. Private evidence with no public row surface remains private.</p></header>
<div class="inspector-table-workspace" data-inspector-table-workspace><div class="table-scroll" tabindex="0"><table data-inspector-table id="current-data-inventory"><caption>Registry datasets and read-only inspection coverage</caption>
<thead><tr><th>Dataset</th><th>Store</th><th>Layer</th><th>Status</th><th>Inspection surface</th></tr></thead><tbody>{dataset_rows}</tbody></table></div></div></section>
</details>"""
    return render_inspector_shell(
        title="Agent Tools", active="/agent-tools", revision=registry.revision,
        body=body, tools=True,
        description="Run current read-only contracts with guided fields and complete, paged strict-JSON results.",
        footer="Loopback only · bounded reads · historical versions remain API compatibility contracts",
    )


__all__ = ("latest_tool_version", "render_current_agent_tools_page")
