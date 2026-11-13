"""Ports (typing.Protocol interfaces) and the dependency containers.

Every integration implements one of these protocols and is selected by
configuration through the registry. Core imports nothing but the standard
library and pydantic; the protocols therefore describe types only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    ApprovalDecision,
    CachedAnswer,
    CacheHit,
    CacheKey,
    ChatRequest,
    CompletionRequest,
    CompletionResponse,
    ContextBudget,
    DeltaCallback,
    EvalCase,
    EvalOutcome,
    FitResult,
    JudgeRequest,
    JudgeVerdict,
    Message,
    ProviderCapabilities,
    RetrievedChunk,
    RunContext,
    RunResult,
    Score,
    ToolResult,
    ToolSpec,
)

__all__ = [
    "Agent",
    "AgentDeps",
    "Cache",
    "ContextPolicy",
    "Embedder",
    "EvalDeps",
    "EvalSuite",
    "Judge",
    "ModelProvider",
    "Orchestrator",
    "ResumableOrchestrator",
    "Retriever",
    "ToolSource",
]


class ModelProvider(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...


class Embedder(Protocol):
    name: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ToolSource(Protocol):
    name: str

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]: ...

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult: ...


class Retriever(Protocol):
    name: str

    async def retrieve(
        self,
        query: str,
        ctx: RunContext,
        k: int = 5,
        *,
        round: int = 1,
        embedding: list[float] | None = None,
    ) -> list[RetrievedChunk]: ...


class Cache(Protocol):
    name: str

    async def key_for(self, ctx: RunContext, query: str) -> CacheKey: ...

    async def lookup(self, key: CacheKey, *, threshold: float = 0.92) -> CacheHit | None: ...

    async def store(self, key: CacheKey, answer: CachedAnswer, *, ttl_days: int = 7) -> None: ...

    async def invalidate(self, tenant_id: str) -> int: ...


class Agent(Protocol):
    spec: AgentSpec

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult: ...


class Orchestrator(Protocol):
    name: str

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult: ...


class ResumableOrchestrator(Protocol):
    """An orchestrator that can pause a run and continue it later (6.7).

    Only the LangGraph path implements this, because only a checkpointed graph
    can resume the exact paused task. ``run`` returns a result whose status is
    ``waiting_approval`` and whose ``pending_approval`` names the tool call;
    ``resume`` takes the decision and runs to the end of that same graph, and
    the API never has to re-run the model planning step. ``ctx`` replaces the
    paused run context (a fresh run token) when the API supplies one.
    """

    name: str

    async def resume(
        self, run_id: str, decision: ApprovalDecision, *, ctx: RunContext | None = None
    ) -> RunResult: ...


class ContextPolicy(Protocol):
    def fit(self, messages: list[Message], budget: ContextBudget) -> FitResult: ...


class Judge(Protocol):
    """Answer quality judge (v2 layer 2, design.md section 6.8).

    A judge reads a finished answer and the evidence the agent saw and returns
    an ``acceptable`` or ``weak`` verdict with a score and a reason. It never
    changes the answer, runs a tool or writes anything: the orchestrator decides
    what a weak verdict means, and the rag suite only reads it.
    """

    name: str

    async def judge(self, request: JudgeRequest) -> JudgeVerdict: ...


class EvalSuite(Protocol):
    name: str

    def load(self, version: str) -> list[EvalCase]: ...

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome: ...

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score: ...


@dataclass(frozen=True)
class AgentDeps:
    """Everything an agent may call. Injected, never imported globally."""

    provider: ModelProvider
    context_policy: ContextPolicy
    ctx: RunContext
    embedder: Embedder | None = None
    tool_sources: list[ToolSource] = field(default_factory=list)
    retriever: Retriever | None = None
    cache: Cache | None = None
    tracer: Any = None
    model_view_rows: int = 20
    context_fill: float = 0.7
    # Set by the orchestrator when the caller wants live answer tokens. An agent
    # attaches it to requests whose answer it is safe to stream (T2.08); None
    # keeps every provider call non-streaming, as in eval runs and unit tests.
    answer_delta: DeltaCallback | None = None


@dataclass(frozen=True)
class EvalDeps:
    """Dependencies handed to an evaluation suite.

    ``provider`` lets the routing and toolcall suites call the model directly,
    one completion per case, instead of running the agents (design.md section
    20.2). ``orchestrator`` and ``context_policy`` stay for suites that execute a
    full run (v1).
    """

    provider: ModelProvider
    orchestrator: Orchestrator | None = None
    context_policy: ContextPolicy | None = None
    tracer: Any = None
