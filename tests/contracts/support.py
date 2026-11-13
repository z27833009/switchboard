"""Shared doubles and settings for the port contract suite.

Every contract module builds its adapters from the entry point registry and
uses these helpers for the parts outside the port under test: a scripted
provider, a run context on the committed tenant configuration, an in-memory
retriever and a deterministic completion builder. Nothing here touches LM
Studio, PostgreSQL or the MCP server; the integration-backed contract tests
open those connections themselves and skip when the environment is absent.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from switchboard.config import load_settings
from switchboard.core.config import Settings, tenant_context
from switchboard.core.errors import RetrievalError
from switchboard.core.models import (
    AgentSpec,
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
    RetrievedChunk,
    RunContext,
    ToolCall,
    Usage,
)
from switchboard.core.ports import Agent, ContextPolicy
from switchboard.registry import AGENTS, Registry

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = REPO_ROOT / "evals" / "datasets"
MOCK_FIXTURES = REPO_ROOT / "evals" / "fixtures" / "mock_provider"
OPENAI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "openai_compatible"
TOOL_MANIFEST = REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml"
CONFIG_DIR = REPO_ROOT / "config"

# Values only have to parse: no contract test in this directory opens a
# connection through them unless it is marked integration and reads the real
# environment instead.
CONFIG_ENV = {
    "LMSTUDIO_BASE_URL": "http://test.invalid/v1",
    "LMSTUDIO_MODEL": "qwen3-8b",
    "LMSTUDIO_EMBEDDING_MODEL": "bge-m3",
    "MCP_OLIST_URL": "http://test.invalid:8001",
}

CAPABILITIES = ProviderCapabilities(
    tools=True,
    json_schema=False,
    streaming=True,
    reasoning=False,
    context_window=8192,
)

TRACE_PARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings(CONFIG_DIR, CONFIG_ENV)


def run_context(
    tenant_id: str = "default",
    *,
    allowed_tools: set[str] | None = None,
    allowed_agents: set[str] | None = None,
) -> RunContext:
    tenant = tenant_context(tenant_id, settings().tenants)
    if allowed_tools is not None:
        tenant = tenant.model_copy(update={"allowed_tools": set(allowed_tools)})
    if allowed_agents is not None:
        tenant = tenant.model_copy(update={"allowed_agents": set(allowed_agents)})
    return RunContext(
        run_id="run-contract",
        tenant=tenant,
        run_token="unused",
        trace_parent=TRACE_PARENT,
    )


def registered_agents() -> dict[str, type[Agent]]:
    classes: dict[str, Any] = Registry().load_all(AGENTS)
    return cast(dict[str, type[Agent]], classes)


def registered_agent_specs() -> list[AgentSpec]:
    return [cls.spec for cls in registered_agents().values()]


def completion(
    text: str | None = None,
    *,
    tool_calls: Sequence[ToolCall] | None = None,
    parsed: dict[str, Any] | None = None,
    usage: Usage | None = None,
    cost_usd: float | None = None,
) -> CompletionResponse:
    return CompletionResponse(
        text=text,
        tool_calls=list(tool_calls or []),
        parsed=parsed,
        usage=usage or Usage(input_tokens=11, output_tokens=3),
        model="contract-model",
        provider="contract",
        latency_ms=1,
        cost_usd=cost_usd,
    )


class ScriptedProvider:
    """Returns queued responses, then a default answer; records every request."""

    name = "contract"

    def __init__(
        self,
        responses: Sequence[CompletionResponse] | None = None,
        *,
        text: str | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._text = text
        self.requests: list[CompletionRequest] = []

    def capabilities(self) -> ProviderCapabilities:
        return CAPABILITIES

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        return completion(self._text if self._text is not None else "")


class InMemoryRetriever:
    """The Retriever contract without a database, for the shared test module."""

    name = "in_memory"

    def __init__(self, chunks: Sequence[RetrievedChunk]) -> None:
        self._chunks = list(chunks)

    async def retrieve(
        self, query: str, ctx: RunContext, k: int = 5, *, round: int = 1
    ) -> list[RetrievedChunk]:
        del query, ctx, round
        if k < 1:
            raise RetrievalError("k must be at least 1")
        return self._chunks[:k]


def chunk(
    ordinal: int,
    *,
    source: str = "returns-policy.md",
    text: str = "Electronics can be returned within 15 calendar days.",
    score: float = 0.9,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{ordinal}",
        document_id=f"doc-{source}",
        source=source,
        title="Returns policy",
        text=text,
        score=score,
        ordinal=ordinal,
    )


def policy_under_test() -> ContextPolicy:
    from switchboard.api.deps import context_policy_for

    return context_policy_for("clear_oldest_tool_results")


__all__ = [
    "CAPABILITIES",
    "CONFIG_DIR",
    "CONFIG_ENV",
    "DATASET_ROOT",
    "MOCK_FIXTURES",
    "OPENAI_FIXTURES",
    "REPO_ROOT",
    "TOOL_MANIFEST",
    "TRACE_PARENT",
    "InMemoryRetriever",
    "ScriptedProvider",
    "chunk",
    "completion",
    "policy_under_test",
    "registered_agent_specs",
    "registered_agents",
    "run_context",
    "settings",
]
