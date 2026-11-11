# ADR 0006: OpenAI-compatible adapter as the first model provider, local models first

Status: proposed, 2026-11-11

## Context

The author runs LM Studio locally and has a DeepSeek API key reserved for later. Both expose OpenAI-compatible APIs. The platform must compare providers on the same test set and keep paid usage under control.

## Decision

The first `ModelProvider` adapter is `OpenAICompatibleProvider`, configured per provider with base URL, key, model name, declared capabilities and a price entry. LM Studio is the Phase 1 default; DeepSeek is enabled in v1 for comparison runs. Capabilities (tools, JSON schema output, context window) are declared in config, not probed, because small local models accept parameters they then ignore. Structured output uses the best strategy the declared capabilities allow (native JSON schema, forced tool call, or prompted JSON with one repair). A `MockProvider` replays recorded fixtures for CI.

## Alternatives

- **LangChain chat model classes.** Already handle many providers. Rejected per ADR 0002; the platform needs its own normalization of tool calls, usage and errors to feed cost accounting and contract tests.
- **LiteLLM as a gateway.** Broad provider coverage. Rejected for Phase 1 as an extra dependency and process; it could become a second adapter later if many providers are wanted.
- **Native Anthropic adapter first.** The author has no key for it now. Planned for v2 to show native tool use alongside the OpenAI-compatible path.

## Consequences

- One adapter covers two providers; the comparison report is apples to apples.
- Eval runs against a paid provider print an estimate and require confirmation; a cap in config refuses larger runs.
- Local model results are expected to be weak on tool calling; the report says so and names the model.
