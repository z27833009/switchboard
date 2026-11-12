# ADR 0002: LangGraph behind an orchestrator port, with an agent loop as the second adapter

Status: proposed, 2026-11-11. Amended 2026-11-12: the second adapter is an agent loop, not a sequential orchestrator.

## Context

LangGraph is the orchestration framework the author knows and the one most job descriptions name. The platform must stay understandable and replaceable, and the orchestration logic (one generic plan graph with fan-out, merge, gate and one corrective action) is small.

Current agent products (Codex, Claude Code, ChatGPT) mostly do not route upfront: one model gets all tools and decides in a loop what to call next. An interviewer will ask why Switchboard routes first. The honest answer needs data, not an argument.

## Decision

- Define an `Orchestrator` port (`run(request, ctx) -> RunResult`). Implement it first with LangGraph. The LangGraph adapter owns the graph definition and state, but every node delegates to an `Agent` or a port; nodes contain no business logic. LangGraph is imported only inside `adapters/orchestrator/langgraph_impl`. Model calls and tool calls inside the graph go through the platform's `ModelProvider` and `ToolSource` ports, not through LangChain model classes or tool bindings.
- In v1, add `AgentLoopOrchestrator` as the second adapter, written without a framework: one model, all of the tenant's tools plus platform tools for document search and result paging, a bounded loop. It reuses providers, tool sources, context policy, gate, cache and tracing. Its plan is derived from the tools it used, so both orchestrators are scored on the same suites and compared in one report (design 6.9).

## Alternatives

- **Use LangChain model classes and tool bindings inside the graph.** Less code in Phase 1. Rejected because provider comparison, cost accounting and the tool permission model all need the platform's own abstractions, and because contract tests for providers would not exist.
- **No framework, hand-written async orchestration for the same plan graph (the v2 `SequentialOrchestrator` of design 0.1).** Would prove the port is real but compare two implementations of the same idea. Replaced by the agent loop, which proves the port and answers a real design question.
- **Agent loop only, no routing.** The mainstream design. Not chosen as the only design because routing may be better on small local models (fewer tools and shorter prompts per call), on cost and on predictability. Measuring it is the point.

## Consequences

- Thin wrappers (about 50 to 150 lines each) replace `langchain-openai` and `langchain-mcp-adapters`.
- LangGraph features such as `Send`, reducers, interrupts (human-in-the-loop) and checkpointing remain available inside the adapter.
- `langgraph-visualizer` can attach as a development tool to this adapter only; the platform's own traces do not depend on it.
- The orchestrator contract tests must be written so both adapters can pass them; they test observable behavior (answer, citations, plan label, spans), not graph structure.
- If the agent loop wins, the README says so (review R13).
