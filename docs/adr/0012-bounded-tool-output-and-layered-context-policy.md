# ADR 0012: Bounded tool output and a layered context policy

Status: accepted, 2026-11-13. Proposed 2026-11-12. Layers 1 to 4 ship: aggregates first, a bounded `ToolResultView` with `result_page@1`, per-agent context, and `ClearOldestToolResults`. Layer 5, model summarization, stays deliberately not implemented.

## Context

Design 0.1 capped tool results at 200 rows and sent them to the model whole. The Phase 1 local model has an 8k context window; 200 rows of order data do not fit next to the system prompt, the tool definitions and the documents. The design had no rule for what happens when a multi-step tool loop fills the context. Agent products handle this in several ways: truncating long tool output and keeping the rest in a file (Claude Code), clearing old tool results (Anthropic's context editing), summarizing the conversation when it nears the limit (Claude Code and Codex compaction), and isolating work in subagents.

## Decision

Apply layers in order of cost and information loss, cheapest and least lossy first:

1. **Shape at the source.** Tools return aggregates first. The MCP row cap stays at 200.
2. **Bounded view plus reference.** The agent keeps the full result in a run-scoped result store. The model sees a `ToolResultView`: summary, total rows, at most 20 rows (configurable), a `truncated` flag and a `result_id`. A platform tool `result_page@1` returns further rows.
3. **Context isolation.** Each agent has its own context; the merge step sees answers and citations, not raw evidence.
4. **Clear old tool results.** Before each model call, `ContextPolicy.fit` estimates tokens. Above 70 percent of the usable window it replaces the oldest consumed tool results with a one-line placeholder, keeping the call record. It never changes the system prompt or the latest result.
5. **Model summarization.** Not implemented.

The default policy is a pure function in core. Token estimation in core is a conservative character heuristic; actual usage reported by the provider is recorded so the estimate's error is visible. Spans record estimated tokens, whether compaction ran and how many results were cleared. The full tool result is attached to the tool span (capped at 64 KB), so the trace shows what the model did not see.

## Alternatives

- **Send everything and rely on a large context window.** Rejected: it fails on the local model, costs more on paid ones, and long contexts degrade answer quality.
- **Summarize with a model when the context is full.** What general-purpose agents do for long sessions. Rejected for Phase 1 and v1 because it is lossy in ways that are hard to measure, costs a model call, and changes the prompt prefix so the provider's prefix cache stops hitting (ADR 0006). Runs here are short (at most 4 tool steps), so layers 1 to 4 should suffice; an eval failure would reopen this.
- **Truncate rows silently.** Rejected: the model would answer "there are 20 late orders" from a truncated preview. The total count and the `truncated` flag prevent that.

## Consequences

- Phase 1 gains a result store, one platform tool and a pure policy function, about 2 hours.
- Clearing old results also changes the prompt prefix, but only late in a long loop, and the span records when it happens.
- The approach scales to the v1 agent loop (8 steps), which is where layer 4 will actually fire.
