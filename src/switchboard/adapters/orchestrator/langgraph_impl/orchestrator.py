"""Generic LangGraph plan graph (design.md sections 6.1 to 6.6)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from opentelemetry import trace
from pydantic import ValidationError

from switchboard.agents.base import zero_usage
from switchboard.agents.merge import MergeAgent
from switchboard.core.config import Limits
from switchboard.core.errors import PlanValidationError, SwitchboardError
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    Budget,
    ChatRequest,
    CompletionRequest,
    ErrorRecord,
    GateVerdict,
    Message,
    RoutingDecision,
    RunContext,
    RunResult,
    RunStatus,
    Usage,
)
from switchboard.core.ports import Agent, AgentDeps, ContextPolicy, Embedder, ModelProvider
from switchboard.core.validation import validate_plan
from switchboard.registry import Registry
from switchboard.tracing.spans import (
    agent_span,
    record_error,
    run_span,
    set_attributes,
    supervisor_span,
)

SUPERVISOR_SYSTEM = (
    "You are Switchboard's routing supervisor. Choose the smallest set of "
    "registered agents that can answer the user's question. direct handles small "
    "talk only. docs answers policy questions from the document index. data "
    "answers order and product questions from read-only tools. Return a JSON "
    "object with an agents array and a one-sentence reason. Do not invent agent "
    "names and do not add an agent that is not needed."
)


def merge_results(
    left: dict[str, AgentResult] | None, right: dict[str, AgentResult] | None
) -> dict[str, AgentResult]:
    return {**(left or {}), **(right or {})}


def add_errors(
    left: list[ErrorRecord] | None, right: list[ErrorRecord] | None
) -> list[ErrorRecord]:
    return [*(left or []), *(right or [])]


def add_usage(left: Usage | None, right: Usage | None) -> Usage:
    return (left or zero_usage()).plus(right or zero_usage())


class RunState(TypedDict, total=False):
    request: ChatRequest
    ctx: RunContext
    started_at: datetime
    plan: RoutingDecision | None
    results: Annotated[dict[str, AgentResult], merge_results]
    answer: AgentResult | None
    verdict: GateVerdict | None
    attempts: int
    errors: Annotated[list[ErrorRecord], add_errors]
    supervisor_usage: Annotated[Usage, add_usage]
    cache_hit: bool
    result: RunResult | None


class BranchState(TypedDict):
    agent_name: str
    request: ChatRequest
    ctx: RunContext


def plan_schema(names: list[str], max_items: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "agents": {
                "type": "array",
                "items": {"type": "string", "enum": names},
                "minItems": 1,
                "maxItems": max_items,
            },
            "reason": {"type": "string"},
        },
        "required": ["agents", "reason"],
    }


def supervisor_prompt(available: list[AgentSpec], query: str) -> str:
    lines = ["Available agents:"]
    lines.extend(f"- {spec.name}: {spec.description}" for spec in available)
    lines.append(
        "Choose one or more agents that can answer the question. Return a JSON "
        "object with an agents array and a reason."
    )
    lines.append(f"Question: {query}")
    return "\n".join(lines)


class LangGraphOrchestrator:
    name = "langgraph"

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
    ) -> None:
        self._provider = provider
        self._context_policy = context_policy
        self._limits = limits or Limits()
        self._embedder = embedder
        self._tool_sources = list(tool_sources or [])
        self._retriever = retriever
        self._cache = cache
        self._tracer = tracer or trace.get_tracer("switchboard.orchestrator.langgraph")
        self._agents = self._load_agents(registry)
        self._graph = self._build_graph()

    @staticmethod
    def _load_agents(registry: Registry) -> dict[str, Agent]:
        agents: dict[str, Agent] = {}
        for value in registry.agents().values():
            agent = value() if isinstance(value, type) else value
            agents[agent.spec.name] = agent
        return agents

    def _needs_met(self, spec: AgentSpec) -> bool:
        for need in spec.needs:
            if need == "tools" and not self._tool_sources:
                return False
            if need == "retriever" and self._retriever is None:
                return False
        return True

    def _available(self, ctx: RunContext) -> list[AgentSpec]:
        specs = [
            agent.spec
            for agent in self._agents.values()
            if agent.spec.name in ctx.tenant.allowed_agents and self._needs_met(agent.spec)
        ]
        return sorted(specs, key=lambda spec: spec.name)

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
        )

    def _build_graph(self) -> Any:
        builder = StateGraph(RunState)
        builder.add_node("cache_lookup", self._cache_lookup)
        builder.add_node("supervisor", self._supervisor)
        builder.add_node("run_agent", self._run_agent)  # type: ignore[arg-type]
        builder.add_node("merge", self._merge)
        builder.add_node("gate", self._gate)
        builder.add_node("cache_store", self._cache_store)
        builder.add_node("finish", self._finish)

        builder.add_edge(START, "cache_lookup")
        builder.add_conditional_edges(
            "cache_lookup", self._route_cache, {"miss": "supervisor", "hit": "finish"}
        )
        builder.add_conditional_edges("supervisor", self._dispatch, ["run_agent"])
        builder.add_edge("run_agent", "merge")
        builder.add_edge("merge", "gate")
        builder.add_edge("gate", "cache_store")
        builder.add_edge("cache_store", "finish")
        builder.add_edge("finish", END)
        return builder.compile()

    def _cache_lookup(self, state: RunState) -> dict[str, Any]:
        return {"cache_hit": False}

    def _route_cache(self, state: RunState) -> str:
        return "hit" if state.get("cache_hit") else "miss"

    async def _supervisor(self, state: RunState) -> dict[str, Any]:
        ctx = state["ctx"]
        with supervisor_span(self._tracer, run_id=ctx.run_id) as span:
            try:
                outputs = await self._plan(state)
            except Exception as exc:
                record_error(span, exc)
                raise
            plan = outputs.get("plan")
            if isinstance(plan, RoutingDecision):
                set_attributes(
                    span,
                    {
                        "switchboard.plan": plan.label,
                        "switchboard.plan.size": len(plan.agents),
                        "switchboard.plan.reason": plan.reason,
                        "switchboard.plan.fallback": any(
                            error.message == "fallback plan used"
                            for error in outputs.get("errors", [])
                        ),
                    },
                )
            return outputs

    async def _plan(self, state: RunState) -> dict[str, Any]:
        ctx = state["ctx"]
        request = state["request"]
        available = self._available(ctx)
        if not available:
            raise PlanValidationError(["no agents are available for this tenant"])
        specs = {spec.name: spec for spec in available}
        schema = plan_schema([spec.name for spec in available], self._limits.max_plan_size)
        messages = [
            Message(role="system", content=SUPERVISOR_SYSTEM),
            Message(role="user", content=supervisor_prompt(available, request.query)),
        ]
        errors: list[ErrorRecord] = []
        usage = zero_usage()

        for attempt in range(2):
            response = await self._provider.complete(
                CompletionRequest(
                    messages=messages,
                    response_schema=schema,
                    temperature=0.0,
                    max_tokens=256,
                    metadata={
                        "purpose": "supervisor.plan",
                        "run_id": ctx.run_id,
                    },
                )
            )
            usage = usage.plus(response.usage)
            try:
                decision = RoutingDecision.model_validate(response.parsed or {})
                validate_plan(
                    decision,
                    agents=specs,
                    max_size=self._limits.max_plan_size,
                )
            except (ValidationError, PlanValidationError) as exc:
                reasons = (
                    "; ".join(exc.reasons) if isinstance(exc, PlanValidationError) else str(exc)
                )
                errors.append(
                    ErrorRecord(
                        kind="PlanValidationError",
                        message=reasons,
                        retryable=True,
                        stage="supervisor",
                    )
                )
                if attempt == 0:
                    messages.append(
                        Message(
                            role="user",
                            content=(
                                "The previous plan was invalid: "
                                f"{reasons}. Return a corrected plan."
                            ),
                        )
                    )
                    continue
                fallback_names = [name for name in self._limits.fallback_plan if name in specs][
                    : self._limits.max_plan_size
                ]
                if not fallback_names:
                    fallback_names = sorted(specs)[: self._limits.max_plan_size]
                fallback = RoutingDecision(
                    agents=fallback_names,
                    reason="Fallback plan after invalid model output.",
                )
                errors.append(
                    ErrorRecord(
                        kind="PlanValidationError",
                        message="fallback plan used",
                        retryable=False,
                        stage="supervisor",
                    )
                )
                return {
                    "plan": fallback,
                    "supervisor_usage": usage,
                    "errors": errors,
                }
            return {
                "plan": decision,
                "supervisor_usage": usage,
                "errors": errors,
            }
        raise PlanValidationError(["supervisor returned no plan"])

    def _dispatch(self, state: RunState) -> list[Send]:
        plan = state.get("plan")
        if plan is None:
            return []
        request = state["request"]
        ctx = state["ctx"]
        return [
            Send(
                "run_agent",
                {"agent_name": name, "request": request, "ctx": ctx},
            )
            for name in plan.agents
        ]

    async def _run_agent(self, branch: BranchState) -> dict[str, Any]:
        name = branch["agent_name"]
        ctx = branch["ctx"]
        with agent_span(self._tracer, agent=name, run_id=ctx.run_id) as span:
            try:
                outputs = await self._run_agent_branch(branch)
            except Exception as exc:
                record_error(span, exc)
                raise
            result = outputs.get("results", {}).get(name)
            if isinstance(result, AgentResult):
                set_attributes(
                    span,
                    {
                        "switchboard.partial": result.partial,
                        "switchboard.citations": len(result.citations),
                    },
                )
            for error in outputs.get("errors", []):
                span.add_event(
                    "agent.error",
                    {
                        "switchboard.error.kind": error.kind,
                        "switchboard.error.stage": error.stage or "",
                    },
                )
            return outputs

    async def _run_agent_branch(self, branch: BranchState) -> dict[str, Any]:
        name = branch["agent_name"]
        request = branch["request"]
        ctx = branch["ctx"]
        agent = self._agents[name]
        budget = Budget(
            max_tool_steps=self._limits.max_tool_steps,
            max_tokens=min(4096, self._provider.capabilities().context_window // 2),
        )
        try:
            result = await agent.run(
                AgentTask(
                    query=request.query,
                    history=request.history,
                    budget=budget,
                ),
                self._deps(ctx),
            )
        except SwitchboardError as exc:
            result = AgentResult(
                answer=f"The {name} agent failed: {exc}",
                usage=zero_usage(),
                partial=True,
            )
            return {
                "results": {name: result},
                "errors": [
                    ErrorRecord(
                        kind=type(exc).__name__,
                        message=str(exc),
                        retryable=exc.retryable,
                        stage=f"agent.{name}",
                    )
                ],
            }
        except Exception as exc:
            result = AgentResult(
                answer=f"The {name} agent failed: {type(exc).__name__}",
                usage=zero_usage(),
                partial=True,
            )
            return {
                "results": {name: result},
                "errors": [
                    ErrorRecord(
                        kind=type(exc).__name__,
                        message=str(exc),
                        stage=f"agent.{name}",
                    )
                ],
            }
        return {"results": {name: result}}

    def _merge(self, state: RunState) -> dict[str, Any]:
        plan = state.get("plan")
        results = state.get("results", {})
        expected = set(plan.agents) if plan is not None else set(results)
        missing = sorted(expected - set(results))
        answer = MergeAgent().merge(results, missing)
        return {"answer": answer}

    def _gate(self, state: RunState) -> dict[str, Any]:
        return {
            "verdict": GateVerdict(passed=True, action="pass"),
            "attempts": state.get("attempts", 0),
        }

    def _cache_store(self, state: RunState) -> dict[str, Any]:
        return {}

    def _finish(self, state: RunState) -> dict[str, Any]:
        ctx = state["ctx"]
        answer = state.get("answer")
        usage = state.get("supervisor_usage", zero_usage())
        if answer is not None:
            usage = usage.plus(answer.usage)
        status: RunStatus = "ok"
        if answer is None or answer.partial:
            status = "partial"
        return {
            "result": RunResult(
                run_id=ctx.run_id,
                tenant_id=ctx.tenant.tenant_id,
                query=state["request"].query,
                plan=state.get("plan"),
                answer=answer,
                status=status,
                usage=usage,
                cache_hit=state.get("cache_hit", False),
                attempts=state.get("attempts", 0),
                errors=state.get("errors", []),
                started_at=state.get("started_at"),
                ended_at=datetime.now(UTC),
            )
        }

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

    async def _execute(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        initial: RunState = {
            "request": request,
            "ctx": ctx,
            "started_at": datetime.now(UTC),
            "plan": None,
            "results": {},
            "answer": None,
            "verdict": None,
            "attempts": 0,
            "errors": [],
            "supervisor_usage": zero_usage(),
            "cache_hit": False,
        }
        try:
            final = await asyncio.wait_for(
                self._graph.ainvoke(initial, {"recursion_limit": 50}),
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
                started_at=initial["started_at"],
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
                started_at=initial["started_at"],
                ended_at=datetime.now(UTC),
            )
        result = final.get("result")
        if not isinstance(result, RunResult):
            raise RuntimeError("the graph finished without a RunResult")
        return result


__all__ = [
    "LangGraphOrchestrator",
    "plan_schema",
    "supervisor_prompt",
]
