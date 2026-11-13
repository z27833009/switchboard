"""Generic LangGraph plan graph (design.md sections 6.1 to 6.6)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send
from opentelemetry import trace
from pydantic import ValidationError

from switchboard.adapters.orchestrator.langgraph_impl.approval import wrap_tool_sources
from switchboard.agents.base import zero_usage
from switchboard.agents.merge import MergeAgent
from switchboard.core.cache import agents_from_label
from switchboard.core.config import Limits, PriceEntry
from switchboard.core.errors import (
    ApprovalUnavailable,
    PlanValidationError,
    SwitchboardError,
    ToolApprovalRejected,
)
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    ApprovalDecision,
    Budget,
    CachedAnswer,
    CacheKey,
    ChatRequest,
    CompletionRequest,
    ErrorRecord,
    GateVerdict,
    Message,
    PendingApproval,
    RoutingDecision,
    RunContext,
    RunResult,
    RunStatus,
    Usage,
)
from switchboard.core.ports import Agent, AgentDeps, ContextPolicy, Embedder, Judge, ModelProvider
from switchboard.core.pricing import add_cost, compute_cost
from switchboard.core.validation import plan_is_cacheable, validate_plan
from switchboard.gate import evaluate_gate, run_judge
from switchboard.registry import Registry
from switchboard.routing import SUPERVISOR_SYSTEM, plan_schema, supervisor_prompt
from switchboard.tracing.spans import (
    agent_span,
    cache_lookup_span,
    cache_store_span,
    gate_span,
    record_error,
    run_span,
    set_attributes,
    supervisor_span,
)

# Design.md section 6.8: at most one corrective action (retry or re-plan) per run.
MAX_CORRECTIVE_ACTIONS = 1

# Called with (run_id, text fragment) when an agent streams its answer. The API
# wires it to the step event queue; eval runs and unit tests leave it None
# (T2.08, design.md 12.5).
AnswerDeltaSink = Callable[[str, str], None]


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
    previous_plan: RoutingDecision | None
    results: Annotated[dict[str, AgentResult], merge_results]
    answer: AgentResult | None
    verdict: GateVerdict | None
    attempts: int
    feedback: str | None
    errors: Annotated[list[ErrorRecord], add_errors]
    supervisor_usage: Annotated[Usage, add_usage]
    # Per-call costs the providers reported, summed the same way as usage. A
    # run that used a fallback provider stays exact because each call carries
    # its own price (design.md 12.4).
    supervisor_cost: Annotated[float | None, add_cost]
    # Every agent invocation, including a first attempt that the gate rejected.
    # The cost of those calls is real, so the run total must include them.
    spent_usage: Annotated[Usage, add_usage]
    spent_cost: Annotated[float | None, add_cost]
    cache_hit: bool
    cache_key: CacheKey | None
    # Set when the user rejected a tool call, so the run ends as ``rejected``
    # with the rejection as its answer and the gate never retries it (6.7).
    approval_rejected: str | None
    result: RunResult | None


class BranchState(TypedDict):
    agent_name: str
    request: ChatRequest
    ctx: RunContext
    feedback: str | None
    query_embedding: list[float] | None


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
        price: PriceEntry | None = None,
        answer_delta: AnswerDeltaSink | None = None,
        judge: Judge | None = None,
        checkpointer: Any = None,
    ) -> None:
        self._provider = provider
        self._context_policy = context_policy
        self._limits = limits or Limits()
        self._embedder = embedder
        # Every tool source is wrapped in the approval gate (T3.01). The gate is
        # inert for a read-only tenant, and a write or ask call pauses the
        # checkpointed graph instead of executing.
        self._tool_sources, self._gates = wrap_tool_sources(list(tool_sources or []))
        self._retriever = retriever
        self._cache = cache
        self._price = price
        self._answer_delta = answer_delta
        self._judge = judge
        # In-process checkpointer: an approval can resume the run while this
        # process lives. Pending approvals do not survive an API restart, which
        # is the documented demo limitation (design.md section 6.7).
        self._checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
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
            answer_delta=(
                None if self._answer_delta is None else partial(self._answer_delta, ctx.run_id)
            ),
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
        builder.add_conditional_edges(
            "gate",
            self._route_gate,
            {
                "cache_store": "cache_store",
                "supervisor": "supervisor",
                "run_agent": "run_agent",
            },
        )
        builder.add_edge("cache_store", "finish")
        builder.add_edge("finish", END)
        # A checkpointer is required for ``interrupt`` (design.md section 6.7);
        # the thread id is the run id, so approve and reject resume this run.
        return builder.compile(checkpointer=self._checkpointer)

    async def _cache_lookup(self, state: RunState) -> dict[str, Any]:
        """Lookup before the supervisor (design.md section 10.1).

        A hit skips every model call and returns the stored plan and answer. A
        miss keeps the key so the docs agent can reuse its embedding. A cache
        error is a miss: the cache can never fail a run.
        """
        request = state["request"]
        ctx = state["ctx"]
        if self._cache is None or not ctx.tenant.cache.enabled or request.history:
            return {"cache_hit": False, "cache_key": None}
        threshold = ctx.tenant.cache.threshold
        with cache_lookup_span(self._tracer, run_id=ctx.run_id, threshold=threshold) as span:
            try:
                key = await self._cache.key_for(ctx, request.query)
                hit = await self._cache.lookup(key, threshold=threshold)
            except Exception as exc:
                record_error(span, exc)
                return {"cache_hit": False, "cache_key": None}
            if hit is None:
                return {"cache_hit": False, "cache_key": key}
            # A hit made no model call, so its stored usage must not be counted
            # again (AC-10d). The embedding is the only cost and it is not a
            # chat token; the local embedder has no price row.
            answer = hit.entry.answer.model_copy(update={"usage": zero_usage(), "cost_usd": 0.0})
            label = hit.entry.plan
            set_attributes(span, {"switchboard.cache.plan": label})
            return {
                "cache_hit": True,
                "cache_key": key,
                "plan": RoutingDecision(
                    agents=agents_from_label(label),
                    reason=f"Cached answer from plan {label}.",
                ),
                "answer": answer,
            }

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
        previous = state.get("previous_plan")
        verdict = state.get("verdict")
        available = self._available(ctx)
        if not available:
            raise PlanValidationError(["no agents are available for this tenant"])
        specs = {spec.name: spec for spec in available}
        schema = plan_schema([spec.name for spec in available], self._limits.max_plan_size)
        messages = [
            Message(role="system", content=SUPERVISOR_SYSTEM),
            Message(
                role="user",
                content=supervisor_prompt(available, request.query, request.history),
            ),
        ]
        if previous is not None:
            gate_reason = verdict.reason if verdict is not None else "the plan was rejected"
            messages.append(
                Message(
                    role="user",
                    content=(
                        f"The previous plan was {previous.label}. "
                        f"The quality gate rejected it: {gate_reason}. "
                        "Choose a different plan that can produce the evidence this "
                        "question needs; the new plan must differ from the previous one."
                    ),
                )
            )
        errors: list[ErrorRecord] = []
        usage = zero_usage()
        cost: float | None = None

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
            cost = add_cost(cost, response.cost_usd)
            try:
                decision = RoutingDecision.model_validate(response.parsed or {})
                validate_plan(
                    decision,
                    agents=specs,
                    max_size=self._limits.max_plan_size,
                )
                if previous is not None and set(decision.agents) == set(previous.agents):
                    raise PlanValidationError(["the new plan must differ from the previous plan"])
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
                fallback_names = self._fallback_names(specs, previous)
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
                    "supervisor_cost": cost,
                    "errors": errors,
                }
            return {
                "plan": decision,
                "supervisor_usage": usage,
                "supervisor_cost": cost,
                "errors": errors,
            }
        raise PlanValidationError(["supervisor returned no plan"])

    def _fallback_names(
        self,
        specs: dict[str, AgentSpec],
        previous: RoutingDecision | None,
    ) -> list[str]:
        """A valid fallback plan, different from the previous one when possible."""
        candidates: list[list[str]] = []
        configured = [name for name in self._limits.fallback_plan if name in specs][
            : self._limits.max_plan_size
        ]
        if configured:
            candidates.append(configured)
        candidates.extend([[name] for name in sorted(specs)])
        for candidate in candidates:
            if previous is None or set(candidate) != set(previous.agents):
                return candidate
        if previous is not None:
            return list(previous.agents)
        return sorted(specs)[: self._limits.max_plan_size]

    def _evidence_agents(self, plan: RoutingDecision) -> list[str]:
        """Agents in the plan whose specs need tools or a retriever (design 6.8)."""
        names: list[str] = []
        for name in plan.agents:
            agent = self._agents.get(name)
            if agent is None:
                continue
            if agent.spec.needs:
                names.append(name)
        return names

    def _dispatch(self, state: RunState) -> list[Send]:
        plan = state.get("plan")
        if plan is None:
            return []
        request = state["request"]
        ctx = state["ctx"]
        key = state.get("cache_key")
        embedding = list(key.embedding) if key is not None and key.embedding else None
        return [
            Send(
                "run_agent",
                {
                    "agent_name": name,
                    "request": request,
                    "ctx": ctx,
                    "feedback": None,
                    "query_embedding": embedding,
                },
            )
            for name in plan.agents
        ]

    async def _run_agent(self, branch: BranchState) -> dict[str, Any]:
        name = branch["agent_name"]
        ctx = branch["ctx"]
        with agent_span(self._tracer, agent=name, run_id=ctx.run_id) as span:
            try:
                outputs = await self._run_agent_branch(branch)
            except GraphBubbleUp:
                # The run is pausing for approval; the graph re-executes this
                # node on resume, so a paused attempt is not an agent error
                # (design.md section 6.7).
                span.set_attribute("switchboard.approval.paused", True)
                raise
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
                    feedback=branch.get("feedback"),
                    budget=budget,
                    query_embedding=branch.get("query_embedding"),
                ),
                self._deps(ctx),
            )
        except ToolApprovalRejected as exc:
            # The user rejected the call, so nothing ran. The run ends with this
            # text as its answer and status ``rejected``; the gate must not turn
            # it into a retry or a re-plan (design.md section 6.7).
            result = AgentResult(
                answer=(
                    f"The tool call {exc.tool} was rejected, so it was not executed "
                    "and nothing was changed. Ask again if you want it to run."
                ),
                usage=zero_usage(),
            )
            return {
                "results": {name: result},
                "spent_usage": result.usage,
                "spent_cost": result.cost_usd,
                "approval_rejected": exc.tool,
                "errors": [
                    ErrorRecord(
                        kind="ToolApprovalRejected",
                        message=str(exc),
                        retryable=False,
                        stage=f"agent.{name}",
                        details={"tool": exc.tool},
                    )
                ],
            }
        except GraphBubbleUp:
            # The approval interrupt is control flow for the graph, not an agent
            # failure: it has to reach LangGraph unchanged so the run can pause.
            raise
        except SwitchboardError as exc:
            result = AgentResult(
                answer=f"The {name} agent failed: {exc}",
                usage=zero_usage(),
                partial=True,
            )
            return {
                "results": {name: result},
                "spent_usage": result.usage,
                "spent_cost": result.cost_usd,
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
                "spent_usage": result.usage,
                "spent_cost": result.cost_usd,
                "errors": [
                    ErrorRecord(
                        kind=type(exc).__name__,
                        message=str(exc),
                        stage=f"agent.{name}",
                    )
                ],
            }
        return {
            "results": {name: result},
            "spent_usage": result.usage,
            "spent_cost": result.cost_usd,
        }

    def _merge(self, state: RunState) -> dict[str, Any]:
        plan = state.get("plan")
        results = state.get("results", {})
        expected = set(plan.agents) if plan is not None else set(results)
        missing = sorted(expected - set(results))
        # A re-plan may leave results from the previous plan in the state; only
        # the sources in the current plan belong in the merged answer.
        selected = {name: result for name, result in results.items() if name in expected}
        answer = MergeAgent().merge(selected, missing)
        return {"answer": answer}

    async def _gate(self, state: RunState) -> dict[str, Any]:
        ctx = state["ctx"]
        plan = state.get("plan")
        answer = state.get("answer")
        results = state.get("results", {})
        if state.get("approval_rejected"):
            # A rejected call is a decision, not a weak answer: no check, no
            # judge, no retry and no re-plan (design.md section 6.7).
            return {"verdict": None}
        with gate_span(self._tracer, run_id=ctx.run_id) as span:
            if plan is None or answer is None:
                verdict = GateVerdict(
                    passed=False,
                    failed_checks=["evidence_present"],
                    action="none",
                    reason="there was no answer to check",
                )
                attempts = state.get("attempts", 0)
            else:
                planned = list(plan.agents)
                produced = [name for name in planned if name in results]
                verdict = evaluate_gate(
                    query=state["request"].query,
                    answer=answer,
                    evidence_agents=self._evidence_agents(plan),
                    expected_sources=planned,
                    result_sources=produced,
                    merged=len(planned) > 1,
                )
                attempts = state.get("attempts", 0)
                if (
                    not verdict.passed
                    and verdict.action in {"retry", "replan"}
                    and attempts >= MAX_CORRECTIVE_ACTIONS
                ):
                    verdict = verdict.model_copy(
                        update={
                            "action": "none",
                            "reason": (
                                f"{verdict.reason}; the attempts limit "
                                f"({MAX_CORRECTIVE_ACTIONS}) is reached"
                            ),
                        }
                    )
            # Layer 2 runs only after layer 1 passes (design 6.8). Its action is
            # always ``none``, so it never starts a retry or re-plan.
            judged = await run_judge(
                self._judge,
                query=state["request"].query,
                answer=answer,
                verdict=verdict,
            )
            verdict = judged.verdict
            set_attributes(
                span,
                {
                    "switchboard.gate.verdict": "pass" if verdict.passed else "fail",
                    "switchboard.gate.failed_checks": (
                        ",".join(verdict.failed_checks) if verdict.failed_checks else None
                    ),
                    "switchboard.gate.action": verdict.action,
                    "switchboard.gate.judge": judged.outcome,
                    "switchboard.gate.judge_score": judged.score,
                },
            )
            outputs: dict[str, Any] = {"verdict": verdict}
            if judged.usage is not None:
                # The judge call is a real model call: its tokens and cost are
                # part of the run total (design 12.4).
                outputs["spent_usage"] = judged.usage
                outputs["spent_cost"] = judged.cost_usd
            if judged.errors:
                outputs["errors"] = list(judged.errors)
            if not verdict.passed:
                outputs["errors"] = [
                    *(outputs.get("errors") or []),
                    ErrorRecord(
                        kind="CheckFailed",
                        message=verdict.feedback or verdict.reason,
                        retryable=verdict.action in {"retry", "replan"},
                        stage="gate",
                        details={
                            "failed_checks": list(verdict.failed_checks),
                            "action": verdict.action,
                        },
                    ),
                ]
            if verdict.action == "retry":
                outputs["attempts"] = attempts + 1
                outputs["feedback"] = verdict.feedback
            elif verdict.action == "replan":
                outputs["attempts"] = attempts + 1
                outputs["previous_plan"] = plan
                outputs["feedback"] = None
            return outputs

    def _route_gate(self, state: RunState) -> str | list[Send]:
        verdict = state.get("verdict")
        if verdict is None or verdict.action in {"pass", "none"}:
            return "cache_store"
        plan = state.get("plan")
        if plan is None:
            return "cache_store"
        if verdict.action == "replan":
            return "supervisor"
        request = state["request"]
        ctx = state["ctx"]
        feedback = state.get("feedback")
        key = state.get("cache_key")
        embedding = list(key.embedding) if key is not None and key.embedding else None
        return [
            Send(
                "run_agent",
                {
                    "agent_name": name,
                    "request": request,
                    "ctx": ctx,
                    "feedback": feedback,
                    "query_embedding": embedding,
                },
            )
            for name in plan.agents
        ]

    async def _cache_store(self, state: RunState) -> dict[str, Any]:
        """Store an answer only when its plan is cacheable and the gate passed."""
        request = state["request"]
        ctx = state["ctx"]
        plan = state.get("plan")
        answer = state.get("answer")
        verdict = state.get("verdict")
        key = state.get("cache_key")
        if (
            self._cache is None
            or key is None
            or plan is None
            or answer is None
            or verdict is None
            or not verdict.passed
            or answer.partial
            or not ctx.tenant.cache.enabled
            or request.history
        ):
            return {}
        specs = {name: agent.spec for name, agent in self._agents.items()}
        if not plan_is_cacheable(plan, specs):
            return {}
        ttl_days = ctx.tenant.cache.ttl_days
        with cache_store_span(
            self._tracer, run_id=ctx.run_id, plan=plan.label, ttl_days=ttl_days
        ) as span:
            try:
                await self._cache.store(
                    key,
                    CachedAnswer(plan=plan.label, answer=answer),
                    ttl_days=ttl_days,
                )
            except Exception as exc:
                record_error(span, exc)
        return {}

    def _finish(self, state: RunState) -> dict[str, Any]:
        ctx = state["ctx"]
        answer = state.get("answer")
        verdict = state.get("verdict")
        # Supervisor calls plus every agent call the run made, including an
        # attempt the gate rejected: those tokens were spent and billed.
        usage = state.get("supervisor_usage", zero_usage()).plus(
            state.get("spent_usage", zero_usage())
        )
        # Exact per-call sum when the providers report costs (a fallback run can
        # mix two price rows). The orchestrator still accepts one price row for
        # callers that provide usage only, such as tests and older adapters.
        cost = add_cost(state.get("supervisor_cost"), state.get("spent_cost"))
        if cost is None and self._price is not None:
            cost = compute_cost(usage, self._price)
        status: RunStatus = "ok"
        if answer is None or answer.partial:
            status = "partial"
        if state.get("approval_rejected"):
            # The run ended on a user decision, so the rejection is the status
            # and the answer above says what was not done (T3.01).
            status = "rejected"
        elif verdict is not None and not verdict.passed:
            status = "partial"
            if (
                answer is not None
                and verdict.action == "none"
                and verdict.failed_checks != ["merge_complete"]
            ):
                note = (
                    "\n\nThe quality gate could not confirm this answer: "
                    f"{verdict.feedback or verdict.reason}"
                )
                answer = answer.model_copy(
                    update={"answer": answer.answer.rstrip() + note, "partial": True}
                )
        # A single-agent plan whose answer is a question back to the user ends
        # the run as needs_clarification. The marker is on the result, not on an
        # agent name, so a new agent needs no change here (design.md 6.4).
        plan = state.get("plan")
        if (
            answer is not None
            and answer.needs_clarification
            and plan is not None
            and len(plan.agents) == 1
            and (verdict is None or verdict.passed)
        ):
            status = "needs_clarification"
        return {
            "result": RunResult(
                run_id=ctx.run_id,
                tenant_id=ctx.tenant.tenant_id,
                query=state["request"].query,
                plan=state.get("plan"),
                previous_plan=state.get("previous_plan"),
                answer=answer,
                status=status,
                usage=usage,
                # Per-call sum when available; one price row covers a legacy
                # single-provider run whose responses carry no cost
                # (design 12.4).
                cost_usd=cost,
                cache_hit=state.get("cache_hit", False),
                attempts=state.get("attempts", 0),
                gate=verdict,
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
            self._set_run_span_attributes(span, result)
            return result

    def _set_run_span_attributes(self, span: trace.Span, result: RunResult) -> None:
        set_attributes(
            span,
            {
                "switchboard.plan": result.plan.label if result.plan else None,
                "switchboard.cache.hit": result.cache_hit,
                "switchboard.attempts": result.attempts,
                "switchboard.status": result.status,
                "switchboard.approval.tool": (
                    result.pending_approval.tool if result.pending_approval else None
                ),
            },
        )
        if result.status in {"ok", "rejected"}:
            span.set_status(trace.Status(trace.StatusCode.OK))
        elif result.status in {"timeout", "provider_error", "auth_error"}:
            span.set_status(trace.Status(trace.StatusCode.ERROR, result.status))

    def _config(self, run_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": run_id},
            "recursion_limit": 50,
        }

    @staticmethod
    def _interrupt_value(final: dict[str, Any]) -> Any:
        interrupts = final.get("__interrupt__")
        if not interrupts:
            return None
        first = interrupts[0]
        return getattr(first, "value", first)

    def _waiting_result(
        self,
        request: ChatRequest,
        ctx: RunContext,
        final: dict[str, Any],
        *,
        started_at: datetime | None,
    ) -> RunResult:
        """The run paused on an approval: report what is waiting, execute nothing."""
        value = self._interrupt_value(final)
        try:
            pending = PendingApproval.model_validate(value)
        except ValidationError as exc:  # pragma: no cover - our own payload
            raise RuntimeError("the approval interrupt carried no tool call") from exc
        return RunResult(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant.tenant_id,
            query=request.query,
            plan=final.get("plan"),
            status="waiting_approval",
            pending_approval=pending,
            usage=final.get("supervisor_usage", zero_usage()),
            errors=list(final.get("errors") or []),
            started_at=started_at,
        )

    async def _execute(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        initial: RunState = {
            "request": request,
            "ctx": ctx,
            "started_at": datetime.now(UTC),
            "plan": None,
            "previous_plan": None,
            "results": {},
            "answer": None,
            "verdict": None,
            "attempts": 0,
            "feedback": None,
            "errors": [],
            "supervisor_usage": zero_usage(),
            "spent_usage": zero_usage(),
            "supervisor_cost": None,
            "spent_cost": None,
            "cache_hit": False,
            "cache_key": None,
            "approval_rejected": None,
        }
        self._reset_approvals(ctx.run_id)
        try:
            final = await asyncio.wait_for(
                self._graph.ainvoke(initial, self._config(ctx.run_id)),
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
        if self._interrupt_value(final) is not None:
            # Keep the thread: approve or reject resumes it. The run row holds
            # the pending call, so the API never trusts a client copy.
            return self._waiting_result(request, ctx, final, started_at=initial["started_at"])
        return self._finished(ctx, final)

    def _finished(self, ctx: RunContext, final: dict[str, Any]) -> RunResult:
        result = final.get("result")
        if not isinstance(result, RunResult):
            raise RuntimeError("the graph finished without a RunResult")
        # The run is over, so the thread is not needed for an approval any more.
        self._drop_thread(ctx.run_id)
        return result

    def _drop_thread(self, run_id: str) -> None:
        delete = getattr(self._checkpointer, "delete_thread", None)
        if delete is None:  # pragma: no cover - a checkpointer without deletion
            return
        with suppress(Exception):  # cleanup only
            delete(run_id)

    def _reset_approvals(self, run_id: str) -> None:
        for gate in self._gates:
            gate.reset(run_id)

    async def resume(
        self,
        run_id: str,
        decision: ApprovalDecision,
        *,
        ctx: RunContext | None = None,
    ) -> RunResult:
        """Continue a paused run with the user's decision (design.md 6.7).

        The paused state carries the request and the run context, so the run
        keeps its tenant and query. A supplied ``ctx`` replaces it, which is how
        the API refreshes an expired run token before the resumed tool call.
        Only the LangGraph adapter implements this; the API answers 409 for an
        orchestrator without it.
        """
        state = await self._graph.aget_state(self._config(run_id))
        values: dict[str, Any] = dict(state.values) if state is not None else {}
        request = values.get("request")
        paused_ctx = values.get("ctx")
        if not isinstance(request, ChatRequest) or not isinstance(paused_ctx, RunContext):
            raise ApprovalUnavailable(
                "the paused state for this run is gone; pending approvals do not "
                "survive an API restart"
            )
        if ctx is not None:
            paused_ctx = ctx
            await self._graph.aupdate_state(self._config(run_id), {"ctx": ctx})
        with run_span(
            self._tracer,
            tenant_id=paused_ctx.tenant.tenant_id,
            run_id=run_id,
            orchestrator=self.name,
        ) as span:
            span.set_attribute("switchboard.approval.resumed", True)
            span.set_attribute("switchboard.approval.decision", decision.approved)
            self._reset_approvals(run_id)
            try:
                final = await asyncio.wait_for(
                    self._graph.ainvoke(
                        Command(resume=decision.model_dump(mode="json")),
                        self._config(run_id),
                    ),
                    timeout=self._limits.run_timeout_s,
                )
            except TimeoutError:
                self._drop_thread(run_id)
                result = RunResult(
                    run_id=run_id,
                    tenant_id=paused_ctx.tenant.tenant_id,
                    query=request.query,
                    status="timeout",
                    errors=[
                        ErrorRecord(
                            kind="RunTimeout",
                            message=f"run exceeded {self._limits.run_timeout_s}s",
                            stage="orchestrator",
                        )
                    ],
                    started_at=values.get("started_at"),
                    ended_at=datetime.now(UTC),
                )
                self._set_run_span_attributes(span, result)
                return result
            if self._interrupt_value(final) is not None:
                # The model asked for another tool that needs approval; the run
                # pauses again with the new call (one decision per call).
                result = self._waiting_result(
                    request, paused_ctx, final, started_at=values.get("started_at")
                )
                self._set_run_span_attributes(span, result)
                return result
            result = self._finished(paused_ctx, final)
            self._set_run_span_attributes(span, result)
            return result


__all__ = [
    "LangGraphOrchestrator",
    "plan_schema",
    "supervisor_prompt",
]
