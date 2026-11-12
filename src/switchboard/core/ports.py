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
    CachedAnswer,
    CacheHit,
    CacheKey,
    ChatRequest,
    CompletionRequest,
    CompletionResponse,
    ContextBudget,
    EvalCase,
    EvalOutcome,
    FitResult,
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
    "ModelProvider",
    "Orchestrator",
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

    async def retrieve(self, query: str, ctx: RunContext, k: int = 5) -> list[RetrievedChunk]: ...


class Cache(Protocol):
    name: str

    async def lookup(self, key: CacheKey) -> CacheHit | None: ...

    async def store(self, key: CacheKey, answer: CachedAnswer) -> None: ...

    async def invalidate(self, tenant_id: str) -> int: ...


class Agent(Protocol):
    spec: AgentSpec

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult: ...


class Orchestrator(Protocol):
    name: str

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult: ...


class ContextPolicy(Protocol):
    def fit(self, messages: list[Message], budget: ContextBudget) -> FitResult: ...


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
