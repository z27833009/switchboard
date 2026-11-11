# ADR 0002: LangGraph behind an orchestrator port

Status: proposed, 2026-11-11

## Context

LangGraph is the orchestration framework the author knows and the one most job descriptions name. The platform must stay understandable and replaceable, and the orchestration logic (four routes, one parallel branch, one re-route) is small.

## Decision

Define an `Orchestrator` port (`run(request, ctx) -> RunResult`). Implement it first with LangGraph. The LangGraph adapter owns the graph definition and state, but every node delegates to an `Agent` or a port; nodes contain no business logic. LangGraph is imported only inside `adapters/orchestrator/langgraph_impl`. Model calls and tool calls inside the graph go through the platform's `ModelProvider` and `ToolSource` ports, not through LangChain model classes or tool bindings.

## Alternatives

- **Use LangChain model classes and tool bindings inside the graph.** Less code in Phase 1. Rejected because provider comparison, cost accounting and the tool permission model all need the platform's own abstractions, and because contract tests for providers would not exist.
- **No framework, hand-written async orchestration.** Shorter for this graph. Deferred to v2 as the second adapter, which proves the port is real.

## Consequences

- Thin wrappers (about 50 to 150 lines each) replace `langchain-openai` and `langchain-mcp-adapters`.
- LangGraph features such as interrupts (human-in-the-loop) and checkpointing remain available inside the adapter.
- `langgraph-visualizer` can attach as a development tool to this adapter only; the platform's own traces do not depend on it.
