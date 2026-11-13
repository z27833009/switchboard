"""Approval gate in front of a tool source (design.md section 6.7, T3.01).

The gate is a ``ToolSource`` decorator that the LangGraph adapter wraps around
every tool source it hands to an agent. A call whose ``ToolSpec.risk`` is
``write``, or whose wire name is in the tenant's ``tools.ask`` list, pauses the
graph with ``interrupt`` before the tool runs. The graph is compiled with a
checkpointer, so ``POST /runs/{id}/approve`` resumes the same task with
``Command(resume=...)`` and the tool executes exactly once. A rejection raises
``ToolApprovalRejected`` and nothing is executed.

Why this lives in the LangGraph adapter and not in core or in the MCP client:
the hook only exists where a graph can be checkpointed. The agent loop withholds
write and ask tools, so it cannot reach this code and cannot bypass approval
(design.md sections 6.7 and 6.9).
"""

from __future__ import annotations

import hashlib
from typing import Any

from langgraph.types import interrupt
from opentelemetry import trace

from switchboard.core.errors import ToolApprovalRejected
from switchboard.core.models import (
    ApprovalDecision,
    PendingApproval,
    RunContext,
    ToolResult,
    ToolSpec,
)
from switchboard.core.ports import ToolSource
from switchboard.tracing.spans import args_hash


def approval_reason(spec: ToolSpec | None, wire_name: str, ctx: RunContext) -> str | None:
    """Why this call needs a decision, or ``None`` when it may run directly.

    The manifest risk is the primary rule; the tenant's ``ask`` list adds a
    decision for a tool the manifest calls read-only. The two are checked
    independently so neither config source can quietly disable the hook.
    """
    if spec is not None and spec.risk == "write":
        return "the tool manifest declares this tool as a write"
    if wire_name in ctx.tenant.ask_tools:
        return "the tenant lists this tool in tools.ask"
    return None


class ApprovalGate:
    """Wrap one ``ToolSource`` and pause before a tool that needs approval."""

    def __init__(self, source: ToolSource) -> None:
        self._source = source
        self.name = source.name
        self._specs: dict[str, ToolSpec] = {}
        # One counter per (run, wire name, argument hash) so the request id of a
        # given call is the same when the node is re-executed after a resume.
        self._counters: dict[tuple[str, str, str], int] = {}
        # Results this run already executed, keyed the same way. A gate retry
        # re-runs the agent and may ask for the same write again; one approval
        # covers one execution of that call per run, never two (T3.01).
        self._executed: dict[tuple[str, str, str], ToolResult] = {}

    def reset(self, run_id: str) -> None:
        """Forget one run's counters and executed calls, before a fresh execution."""
        for mapping in (self._counters, self._executed):
            for key in [key for key in mapping if key[0] == run_id]:
                del mapping[key]

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        specs = await self._source.list_tools(ctx)
        for spec in specs:
            self._specs[spec.wire_name] = spec
        return specs

    def _request(
        self,
        ctx: RunContext,
        wire_name: str,
        args: dict[str, Any],
        digest: str,
        reason: str,
    ) -> PendingApproval:
        key = (ctx.run_id, wire_name, digest)
        occurrence = self._counters.get(key, 0)
        self._counters[key] = occurrence + 1
        request_id = hashlib.sha256(
            f"{ctx.run_id}|{wire_name}|{digest}|{occurrence}".encode()
        ).hexdigest()[:16]
        return PendingApproval(
            request_id=request_id,
            tool=wire_name,
            arguments=args,
            reason=reason,
        )

    @staticmethod
    def _decision(decision: Any, request_id: str) -> ApprovalDecision | None:
        try:
            parsed = ApprovalDecision.model_validate(decision)
        except Exception:
            return None
        return parsed if parsed.request_id == request_id else None

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        reason = approval_reason(self._specs.get(name), name, ctx)
        if reason is None:
            return await self._source.call(name, args, ctx)

        digest = args_hash(args)
        key = (ctx.run_id, name, digest)
        span = trace.get_current_span()
        span.set_attribute("switchboard.approval.tool", name)
        already = self._executed.get(key)
        if already is not None:
            # The approved call already ran in this run (a gate retry asked for
            # it again): reuse the result instead of writing a second time.
            span.set_attribute("switchboard.approval.reused", True)
            return already

        request = self._request(ctx, name, args, digest, reason)
        span.set_attribute("switchboard.approval.reason", reason)
        span.set_attribute("switchboard.approval.request_id", request.request_id)

        # The value is the payload the API stores on the run row. LangGraph
        # matches resume values to interrupts in call order, so a node that is
        # re-executed reads the decision here and continues.
        decision = self._decision(interrupt(request.model_dump(mode="json")), request.request_id)
        if decision is None:
            # The resumed value was not an answer to this call (the model chose
            # a different call on re-execution, or the client sent something
            # unusable). Ask again for the call that is actually being made.
            decision = self._decision(
                interrupt(request.model_dump(mode="json")), request.request_id
            )
        if decision is None or not decision.approved:
            span.set_attribute("switchboard.approval.decision", "rejected")
            raise ToolApprovalRejected(name)
        span.set_attribute("switchboard.approval.decision", "approved")
        result = await self._source.call(name, args, ctx)
        self._executed[key] = result
        return result


def wrap_tool_sources(sources: list[ToolSource]) -> tuple[list[ToolSource], list[ApprovalGate]]:
    """Wrap every source; return the wrapped list and the gates to reset per run."""
    gates = [ApprovalGate(source) for source in sources]
    return list(gates), gates


__all__ = ["ApprovalGate", "approval_reason", "wrap_tool_sources"]
