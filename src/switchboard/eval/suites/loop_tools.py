"""Tool list offered by the first agent-loop step (design.md sections 6.9, 7.5).

The routing and toolcall suites both measure that first decision, and the
production adapter exposes the same set: the tenant's read-only tools plus the
platform tools ``result_page@1`` and, only when a retriever is configured,
``search_docs@1``. One helper keeps the two suites from drifting apart.
"""

from __future__ import annotations

from collections.abc import Iterable

from switchboard.adapters.tools.platform import search_docs_spec
from switchboard.agents.results import result_page_spec
from switchboard.core.models import ToolSpec


def agent_loop_tool_specs(
    tenant_tools: Iterable[ToolSpec],
    *,
    retriever_configured: bool,
) -> list[ToolSpec]:
    tools = [spec for spec in tenant_tools if spec.risk == "read"]
    tools.append(result_page_spec())
    if retriever_configured:
        tools.append(search_docs_spec())
    return sorted(tools, key=lambda spec: spec.wire_name)


__all__ = ["agent_loop_tool_specs"]
