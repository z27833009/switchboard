# ADR 0006: OpenAI-compatible adapter as the first model provider, local models first

Status: accepted, 2026-11-13. Proposed 2026-11-11, amended 2026-11-12 (prompt layout for provider prefix caching, cached token accounting, reasoning capability). Shipped with LM Studio as the measured provider. DeepSeek is configured and priced but not live-tested: the six DeepSeek rows in `evals/reports/compare.md` are `not run` because no key is available, so the provider comparison is pending.

## Context

The author runs LM Studio locally and has a DeepSeek API key reserved for later. Both expose OpenAI-compatible APIs. The platform must compare providers on the same test set and keep paid usage under control.

Most providers cache identical prompt prefixes and bill cached input tokens at a lower rate (DeepSeek does this by default; Anthropic and OpenAI offer it too). For an agent platform that sends the same system prompt and tool definitions on every call, this is the largest cost lever available, and it needs no infrastructure.

## Decision

- The first `ModelProvider` adapter is `OpenAICompatibleProvider`, configured per provider with base URL, key, model name and declared capabilities. LM Studio is the Phase 1 default; DeepSeek is enabled in v1 for comparison runs.
- The DeepSeek entry was verified on 2026-11-12 against its price page and chat-completions reference: `deepseek-flash` and `deepseek-v4-pro`, 1M context, tools and streaming supported, thinking on by default, and `response_format` limited to `json_object` rather than a JSON schema. The capability block therefore declares `json_schema: false`, and `extra_body: {reasoning_effort: none}` keeps the forced tool-call strategy valid (named or required tool choice is rejected in thinking mode).
- Capabilities (tools, JSON schema output, streaming, reasoning, context window) are declared in config, not probed, because small local models accept parameters they then ignore.
- Structured output uses the best strategy the declared capabilities allow (native JSON schema, forced tool call, or prompted JSON with one repair).
- Usage is normalized to input, cached input and output tokens, reading cached tokens from whichever field the provider returns and defaulting to zero.
- Prompt builders follow a fixed layout: static system prompt, sorted tool definitions and static instructions first; tenant facts, history, query and evidence last. A unit test asserts byte-identical prefixes across different queries (design 8.5).
- A `MockProvider` replays recorded fixtures for CI.
- A provider may name one `fallback_provider`. A `FallbackProvider` decorator retries transient transport failures within the configured budget and then calls the fallback; non-retryable request errors fail fast. Agents and the orchestrator see one `ModelProvider` (design 8.4).

## Alternatives

- **LangChain chat model classes.** Already handle many providers. Rejected per ADR 0002; the platform needs its own normalization of tool calls, usage and errors to feed cost accounting and contract tests.
- **LiteLLM as a gateway.** Broad provider coverage. Rejected for Phase 1 as an extra dependency and process; it could become a second adapter later if many providers are wanted.
- **Native Anthropic adapter first.** The author has no key for it now. Planned for v2 to show native tool use and explicit cache control alongside the OpenAI-compatible path.
- **Ignore prefix caching, rely on the semantic cache for cost.** Rejected: the semantic cache helps only repeated questions; prefix caching helps every call.

## Consequences

- One adapter covers two providers; the comparison report is apples to apples.
- Eval runs against a paid provider print an estimate and require confirmation; a cap in config refuses larger runs.
- Local model results are expected to be weak on tool calling; the report says so and names the model.
- Reports show the cached share of input tokens per provider; on LM Studio it is zero by construction.
- Prompt changes must respect the layout; putting a timestamp in the system prompt would silently disable caching, and the prefix test catches it.
