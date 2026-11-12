"""Agent loop orchestrator adapter: one model, every read-only tool, a bounded loop.

Design.md section 6.9 and ADR 0002. This adapter has no supervisor and no
fan-out. One model receives every tool the tenant may call (each ``ToolSource``
already filters its list by the tenant allowlist) plus the platform tools
``result_page@1`` and, when a retriever is configured, ``search_docs@1``. It
loops up to ``limits.max_loop_steps`` model calls: a response with tool calls
runs them and continues, a response without one is the answer.

The plan does not exist before the run, so it is derived from what the loop
actually did (:func:`switchboard.agent_loop.derive_plan`). That is what makes
both orchestrators comparable on the routing dataset (AC-16b). Because the plan
is known only at the end, it is recorded on a ``supervisor.plan`` span after the
loop, so the trace and the step stream carry the same ``plan.decided`` event as
the LangGraph path (design 12.1 and 12.5).

The adapter reuses the LangGraph path's provider, tool sources, context policy,
result store, gate and tracing. The semantic cache is still a pass-through
(T2.18), exactly as in the LangGraph adapter. Every step carries tools, so the
loop never streams answer deltas (design 8.1). A write tool is never exposed and
never executed, and a corrective action is one more loop with the gate's
feedback: without a supervisor there is nothing to re-plan against, so the next
loop is the model's chance to choose different tools.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from opentelemetry import trace

from switchboard.adapters.tools.platform import SearchDocsToolSource
from switchboard.agent_loop import (
    AGENT_LOOP_AGENT_NAME,
    AGENT_LOOP_INSTRUCTIONS,
    AGENT_LOOP_PURPOSE,
    AGENT_LOOP_SYSTEM,
    DOCS_TOOL,
    base_tool_name,
    derive_plan,
    loop_messages,
)
from switchboard.agents.base import BaseAgent, zero_usage
from switchboard.agents.citations import CitationRegistry
from switchboard.agents.results import ResultPageToolSource, ResultStore, citation_snippet
from switchboard.core.config import Limits, PriceEntry
from switchboard.core.errors import (
    RetrievalError,
    RunTokenError,
    SwitchboardError,
    ToolExecutionError,
    ToolPermissionError,
    ToolValidationError,
)
from switchboard.core.guardrails import scan, wrap_data_block
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    Budget,
    ChatRequest,
    Citation,
    ErrorRecord,
    EvidenceRef,
    GateVerdict,
    Message,
    RoutingDecision,
    RunContext,
    RunResult,
    RunStatus,
    ToolCall,
    ToolCallRecord,
    ToolSpec,
)
from switchboard.core.ports import (
    AgentDeps,
    ContextPolicy,
    Embedder,
    ModelProvider,
    ToolSource,
)
from switchboard.core.pricing import add_cost, compute_cost
from switchboard.gate import evaluate_gate
from switchboard.registry import Registry
from switchboard.tracing.spans import (
    agent_span,
    gate_span,
    record_error,
    run_span,
    set_attributes,
    supervisor_span,
)

# Design.md section 6.8: at most one corrective action per run.
MAX_CORRECTIVE_ACTIONS = 1

# Called with (run_id, text fragment) when a step streams an answer. The loop
# never streams because every step carries tools, but the API passes the sink so
# both orchestrators are built the same way (T2.08, design.md 12.5).
AnswerDeltaSink = Callable[[str, str], None]


class _LoopStep(BaseAgent):
    """Only used for the shared completion path of :mod:`switchboard.agents.base`.

    The loop is not a routable agent; this object exists so the context policy,
    the non-streaming rule and the request metadata are the same code as in the
    agents (design 8.5, 8.6 and 12.1).
    """

    spec = AgentSpec(
        name=AGENT_LOOP_AGENT_NAME,
        description="Internal completion helper for the agent loop orchestrator.",
        cacheable=False,
    )
    system_prompt = AGENT_LOOP_SYSTEM
    instructions = AGENT_LOOP_INSTRUCTIONS


@dataclass
class _LoopTrace:
    """What one loop attempt saw, in the shape :class:`AgentResult` expects."""

    messages: list[Message]
    records: list[ToolCallRecord] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    registry: CitationRegistry = field(default_factory=CitationRegistry)
    calls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _LoopRun:
    answer: AgentResult
    plan: RoutingDecision


class AgentLoopOrchestrator:
    name = "agent_loop"

    def __init__(
        self,
        *,
        provider: ModelProvider,
        registry: Registry,
        context_policy: ContextPolicy,
        limits: Limits | None = None,
        embedder: Embedder | None = None,
        tool_sources: list[Any] | None = None,
        retriever: Any = None,
        cache: Any = None,
        tracer: Any = None,
        price: PriceEntry | None = None,
        answer_delta: AnswerDeltaSink | None = None,
    ) -> None:
        self._provider = provider
        self._context_policy = context_policy
        self._limits = limits or Limits()
        self._embedder = embedder
        self._tool_sources = list(tool_sources or [])
        self._retriever = retriever
        self._cache = cache
        self._price = price
        self._answer_delta = answer_delta
        self._tracer = tracer or trace.get_tracer("switchboard.orchestrator.agent_loop")
        self._agents = self._load_agents(registry)
        self._step = _LoopStep()

    @staticmethod
    def _load_agents(registry: Registry) -> dict[str, Any]:
        agents: dict[str, Any] = {}
        for value in registry.agents().values():
            agent = value() if isinstance(value, type) else value
            agents[agent.spec.name] = agent
        return agents

    def _deps(self, ctx: RunContext) -> AgentDeps:
        return AgentDeps(
            provider=self._provider,
            context_policy=self._context_policy,
            ctx=ctx,
            embedder=self._embedder,
            tool_sources=list(self._tool_sources),
            retriever=self._retriever,
            cache=self._cache,
            tracer=self._tracer,
            model_view_rows=self._limits.model_view_rows,
            context_fill=self._limits.context_fill,
            answer_delta=(
                None if self._answer_delta is None else partial(self._answer_delta, ctx.run_id)
            ),
        )

    def _budget(self) -> Budget:
        return Budget(
            max_tool_steps=self._limits.max_tool_steps,
            max_tokens=min(4096, self._provider.capabilities().context_window // 2),
        )

    async def _collect_tools(
        self, deps: AgentDeps
    ) -> tuple[list[ToolSpec], dict[str, ToolSource], ResultStore]:
        """Every read-only tool the run may call, sorted by wire name.

        ``ResultStore`` is per attempt, so a ``result_id`` from a rejected first
        attempt cannot leak into the next one. A spec with ``risk == "write"`` is
        dropped before the model can see it, and a tool in the tenant's ``ask``
        list is dropped too because approval does not exist yet (design 6.7).
        """
        store = ResultStore()
        sources: list[ToolSource] = [*deps.tool_sources, ResultPageToolSource(store)]
        if deps.retriever is not None:
            sources.append(SearchDocsToolSource(deps.retriever))
        specs: dict[str, ToolSpec] = {}
        owners: dict[str, ToolSource] = {}
        for source in sources:
            for spec in await source.list_tools(deps.ctx):
                if spec.risk != "read" or spec.wire_name in deps.ctx.tenant.ask_tools:
                    continue
                specs[spec.wire_name] = spec
                owners[spec.wire_name] = source
        return [specs[name] for name in sorted(specs)], owners, store

    async def _loop_once(
        self,
        request: ChatRequest,
        ctx: RunContext,
        *,
        feedback: str | None,
    ) -> _LoopRun:
        """Run one bounded loop and derive the plan from the calls it made."""
        deps = self._deps(ctx)
        tools, owners, store = await self._collect_tools(deps)
        known = [spec.wire_name for spec in tools]
        task = AgentTask(
            query=request.query,
            history=request.history,
            feedback=feedback,
            budget=self._budget(),
        )
        trace = _LoopTrace(messages=loop_messages(request.query, request.history, feedback))
        usage = zero_usage()
        cost: float | None = None
        last_text = ""

        for _ in range(self._limits.max_loop_steps):
            response = await self._step.complete(trace.messages, deps, task, tools)
            usage = usage.plus(response.usage)
            cost = add_cost(cost, response.cost_usd)
            last_text = response.text or last_text
            if not response.tool_calls:
                answer_text = response.text or ""
                return _LoopRun(
                    answer=AgentResult(
                        answer=answer_text,
                        citations=trace.registry.parse(answer_text),
                        tool_calls=trace.records,
                        evidence=trace.evidence,
                        usage=usage,
                        cost_usd=cost,
                        # A run that called a tool but saw no usable evidence must
                        # say so; a direct answer needs no evidence (design 6.4).
                        partial=bool(answer_text) and bool(trace.calls) and not trace.evidence,
                    ),
                    plan=derive_plan(trace.calls, known),
                )
            trace.messages.append(
                Message(
                    role="assistant",
                    content=response.text or "",
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                trace.calls.append(call.name)
                category = "docs" if base_tool_name(call.name) == DOCS_TOOL else "data"
                with agent_span(self._tracer, agent=category, run_id=ctx.run_id) as span:
                    status = await self._execute_call(call, owners, store, deps, trace)
                    set_attributes(
                        span,
                        {
                            "switchboard.partial": status != "ok",
                            "switchboard.citations": len(trace.evidence),
                        },
                    )

        answer_text = last_text or "I reached the step limit before I could answer the question."
        return _LoopRun(
            answer=AgentResult(
                answer=answer_text,
                citations=trace.registry.parse(last_text),
                tool_calls=trace.records,
                evidence=trace.evidence,
                usage=usage,
                cost_usd=cost,
                partial=True,
            ),
            plan=derive_plan(trace.calls, known),
        )

    async def _execute_call(
        self,
        call: ToolCall,
        owners: dict[str, ToolSource],
        store: ResultStore,
        deps: AgentDeps,
        trace: _LoopTrace,
    ) -> str:
        """Execute one call and append its record, view and evidence (6.4, 8.6).

        Permission and validation failures go back to the model as a tool error,
        never as an exception, so the run continues with the sources it has. The
        returned status is ``ok``, ``error`` or ``denied`` for the agent span.
        """
        source = owners.get(call.name)
        if source is None:
            # Includes a write tool the model named although it was never
            # exposed: nothing is executed, the model is told it is unknown.
            message = f"unknown tool {call.name}"
            trace.messages.append(
                Message(
                    role="tool",
                    content=json.dumps({"error": message}),
                    tool_call_id=call.call_id,
                )
            )
            trace.records.append(
                ToolCallRecord(
                    tool=call.name,
                    arguments=call.arguments,
                    status="error",
                    error=message,
                )
            )
            return "error"
        try:
            result = await source.call(call.name, call.arguments, deps.ctx)
        except ToolPermissionError as exc:
            trace.messages.append(
                Message(
                    role="tool",
                    content=json.dumps({"error": f"permission denied: {exc}"}),
                    tool_call_id=call.call_id,
                )
            )
            trace.records.append(
                ToolCallRecord(
                    tool=call.name,
                    arguments=call.arguments,
                    status="denied",
                    error=str(exc),
                )
            )
            return "denied"
        except (ToolValidationError, ToolExecutionError, RetrievalError) as exc:
            trace.messages.append(
                Message(
                    role="tool",
                    content=json.dumps({"error": str(exc)}),
                    tool_call_id=call.call_id,
                )
            )
            trace.records.append(
                ToolCallRecord(
                    tool=call.name,
                    arguments=call.arguments,
                    status="error",
                    error=str(exc),
                )
            )
            return "error"

        if result.tool == "result_page@1" and result.result_id:
            result_id = result.result_id
        else:
            result_id = store.add(result)
        view = result.view(limit=deps.model_view_rows, result_id=result_id)
        body = view.model_dump_json()
        ref = f"t{len(trace.evidence) + 1}"
        trace.registry.add(
            Citation(
                kind="tool",
                ref=ref,
                source_id=result_id,
                audit_id=result.audit_id,
                snippet=citation_snippet(result, view),
            )
        )
        trace.evidence.append(
            EvidenceRef(
                kind="tool",
                ref=ref,
                source_id=result_id,
                text=view.model_dump_json(),
                empty=view.total_rows == 0,
            )
        )
        trace.messages.append(
            Message(
                role="tool",
                content=wrap_data_block(
                    "tool_result",
                    body,
                    attributes={
                        "ref": ref,
                        "tool": call.name,
                        "result_id": result_id,
                    },
                    result=scan(body),
                ),
                tool_call_id=call.call_id,
                result_id=result_id,
                total_rows=view.total_rows,
            )
        )
        trace.records.append(
            ToolCallRecord(
                tool=call.name,
                arguments=call.arguments,
                status="ok",
                result_id=result_id,
                audit_id=result.audit_id,
                duration_ms=result.duration_ms,
            )
        )
        return "ok"

    def _evidence_agents(self, plan: RoutingDecision) -> list[str]:
        """Plan agents whose specs need tools or a retriever (design 6.8)."""
        names: list[str] = []
        for name in plan.agents:
            agent = self._agents.get(name)
            if agent is None:
                continue
            if agent.spec.needs:
                names.append(name)
        return names

    def _evaluate(
        self,
        query: str,
        plan: RoutingDecision,
        answer: AgentResult,
    ) -> GateVerdict:
        return evaluate_gate(
            query=query,
            answer=answer,
            evidence_agents=self._evidence_agents(plan),
            expected_sources=list(plan.agents),
            result_sources=list(plan.agents),
            merged=len(plan.agents) > 1,
        )

    async def _gated(
        self,
        request: ChatRequest,
        ctx: RunContext,
        started_at: datetime,
    ) -> RunResult:
        attempts = 0
        previous_plan: RoutingDecision | None = None
        feedback: str | None = None
        spent = zero_usage()
        spent_cost: float | None = None
        errors: list[ErrorRecord] = []
        answer: AgentResult | None = None
        verdict: GateVerdict | None = None

        while True:
            loop_run = await self._loop_once(request, ctx, feedback=feedback)
            answer = loop_run.answer
            plan = loop_run.plan
            spent = spent.plus(answer.usage)
            spent_cost = add_cost(spent_cost, answer.cost_usd)

            # The plan exists only now, so the trace gets it after the tool work
            # but before the gate, and the step stream still sees plan.decided.
            with supervisor_span(self._tracer, run_id=ctx.run_id) as span:
                set_attributes(
                    span,
                    {
                        "switchboard.plan": plan.label,
                        "switchboard.plan.size": len(plan.agents),
                        "switchboard.plan.reason": plan.reason,
                        "switchboard.plan.fallback": False,
                    },
                )

            with gate_span(self._tracer, run_id=ctx.run_id) as span:
                verdict = self._evaluate(request.query, plan, answer)
                if verdict.action in {"retry", "replan"} and attempts >= MAX_CORRECTIVE_ACTIONS:
                    verdict = verdict.model_copy(
                        update={
                            "action": "none",
                            "reason": (
                                f"{verdict.reason}; the attempts limit "
                                f"({MAX_CORRECTIVE_ACTIONS}) is reached"
                            ),
                        }
                    )
                set_attributes(
                    span,
                    {
                        "switchboard.gate.verdict": "pass" if verdict.passed else "fail",
                        "switchboard.gate.failed_checks": (
                            ",".join(verdict.failed_checks) if verdict.failed_checks else None
                        ),
                        "switchboard.gate.action": verdict.action,
                    },
                )

            if not verdict.passed:
                errors.append(
                    ErrorRecord(
                        kind="CheckFailed",
                        message=verdict.feedback or verdict.reason,
                        retryable=verdict.action in {"retry", "replan"},
                        stage="gate",
                        details={
                            "failed_checks": list(verdict.failed_checks),
                            "action": verdict.action,
                        },
                    )
                )
            if verdict.action in {"retry", "replan"}:
                attempts += 1
                if verdict.action == "replan":
                    previous_plan = plan
                feedback = verdict.feedback or verdict.reason
                continue
            break

        # The loop body always runs at least once and leaves both values set.
        assert answer is not None and verdict is not None
        status: RunStatus = "ok"
        if answer.partial:
            status = "partial"
        if not verdict.passed:
            status = "partial"
            if verdict.action == "none" and verdict.failed_checks != ["merge_complete"]:
                note = (
                    "\n\nThe quality gate could not confirm this answer: "
                    f"{verdict.feedback or verdict.reason}"
                )
                answer = answer.model_copy(
                    update={"answer": answer.answer.rstrip() + note, "partial": True}
                )

        cost = spent_cost
        if cost is None and self._price is not None:
            cost = compute_cost(spent, self._price)
        return RunResult(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant.tenant_id,
            query=request.query,
            plan=plan,
            previous_plan=previous_plan,
            answer=answer,
            status=status,
            usage=spent,
            cost_usd=cost,
            cache_hit=False,
            attempts=attempts,
            gate=verdict,
            errors=errors,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )

    async def _execute(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        started_at = datetime.now(UTC)
        try:
            return await asyncio.wait_for(
                self._gated(request, ctx, started_at),
                timeout=self._limits.run_timeout_s,
            )
        except TimeoutError:
            return RunResult(
                run_id=ctx.run_id,
                tenant_id=ctx.tenant.tenant_id,
                query=request.query,
                status="timeout",
                errors=[
                    ErrorRecord(
                        kind="RunTimeout",
                        message=f"run exceeded {self._limits.run_timeout_s}s",
                        stage="orchestrator",
                    )
                ],
                started_at=started_at,
                ended_at=datetime.now(UTC),
            )
        except RunTokenError as exc:
            return RunResult(
                run_id=ctx.run_id,
                tenant_id=ctx.tenant.tenant_id,
                query=request.query,
                status="auth_error",
                errors=[
                    ErrorRecord(
                        kind=type(exc).__name__,
                        message=str(exc),
                        retryable=exc.retryable,
                        stage="orchestrator",
                    )
                ],
                started_at=started_at,
                ended_at=datetime.now(UTC),
            )
        except SwitchboardError as exc:
            return RunResult(
                run_id=ctx.run_id,
                tenant_id=ctx.tenant.tenant_id,
                query=request.query,
                status="provider_error",
                errors=[
                    ErrorRecord(
                        kind=type(exc).__name__,
                        message=str(exc),
                        retryable=exc.retryable,
                        stage="orchestrator",
                    )
                ],
                started_at=started_at,
                ended_at=datetime.now(UTC),
            )

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        with run_span(
            self._tracer,
            tenant_id=ctx.tenant.tenant_id,
            run_id=ctx.run_id,
            orchestrator=self.name,
        ) as span:
            try:
                result = await self._execute(request, ctx)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                record_error(span, exc)
                raise
            set_attributes(
                span,
                {
                    "switchboard.plan": result.plan.label if result.plan else None,
                    "switchboard.cache.hit": result.cache_hit,
                    "switchboard.attempts": result.attempts,
                    "switchboard.status": result.status,
                },
            )
            if result.status == "ok":
                span.set_status(trace.Status(trace.StatusCode.OK))
            elif result.status in {"timeout", "provider_error", "auth_error"}:
                span.set_status(trace.Status(trace.StatusCode.ERROR, result.status))
            return result


__all__ = [
    "AGENT_LOOP_PURPOSE",
    "AgentLoopOrchestrator",
    "AnswerDeltaSink",
]
