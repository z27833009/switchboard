"""Suite construction through the entry point registry (ADR 0008 and 0011).

A suite class is registered under ``switchboard.eval_suites`` and gets the fixed
setup it needs through ``from_context``: the dataset root, the registered agent
specs and the Olist tool specs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from switchboard.core.config import Settings
from switchboard.core.errors import ConfigError
from switchboard.core.models import AgentSpec, ToolSpec
from switchboard.core.ports import EvalSuite
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT
from switchboard.registry import EVAL_SUITES, Registry


@dataclass(frozen=True)
class SuiteContext:
    dataset_root: Path = DEFAULT_DATASET_ROOT
    agent_specs: list[AgentSpec] = field(default_factory=list)
    tools: list[ToolSpec] = field(default_factory=list)
    max_plan_size: int = 3
    # The rag suite needs the configured embedder and tenant to open the
    # document index; suites that make no database call ignore it.
    settings: Settings | None = None
    # Report dimension. Routing and toolcall run a second orchestrator
    # (design.md section 20.2); rag carries it as the comparison dimension only,
    # because retrieval is shared by both adapters. The deterministic suites have
    # no orchestrator dimension and the CLI rejects the flag for them.
    orchestrator: str = "langgraph"
    # The eval provider name. The cache suite uses it to choose the embedder:
    # ``mock`` runs the deterministic fake for CI mechanics, everything else
    # uses the platform embedder (bge-m3 on LM Studio).
    provider: str = "mock"
    # The default tenant's read-only tool specs, which the routing and toolcall
    # suites offer (agent-loop mode, and the toolcall suite's data prompt). The
    # MCP source filters by tenant at run time; the eval has no run context, so
    # the CLI passes the filtered list.
    tenant_tools: list[ToolSpec] = field(default_factory=list)
    # search_docs@1 is a platform tool that only exists when a retriever is
    # configured, so the agent-loop prompt must not offer it otherwise.
    retriever_configured: bool = True


def create_suite(registry: Registry, name: str, context: SuiteContext) -> EvalSuite:
    component: Any = registry.load(EVAL_SUITES, name)
    builder = getattr(component, "from_context", None)
    if builder is None:
        raise ConfigError(f"eval suite adapter {name!r} has no from_context")
    return cast(EvalSuite, builder(context))


__all__ = ["SuiteContext", "create_suite"]
