# ADR 0006: OpenAI-compatible adapter as the first model provider, local models first

Status: accepted, 2026-11-13. Proposed 2026-11-11, amended 2026-11-12 (prompt layout for provider prefix caching, cached token accounting, reasoning capability) and 2026-11-13 (native Anthropic adapter as the second, non-OpenAI-compatible adapter; usage gained cache write tokens). Shipped with LM Studio as the first measured provider. DeepSeek was measured on 2026-11-13 for the six comparison rows; the twelve-row table is in `evals/reports/compare.md`. DeepSeek rejects `@` in function names, so the adapter maps sanitised names on the wire and back to the wire name on the response. The Anthropic adapter (T3.06) is implemented and contract-tested with an injected client but was never called live: no key was available, so it is not a measured provider and no tenant selects it.

## Context

The author runs LM Studio locally and has a DeepSeek API key reserved for later. Both expose OpenAI-compatible APIs. The platform must compare providers on the same test set and keep paid usage under control.

Most providers cache identical prompt prefixes and bill cached input tokens at a lower rate (DeepSeek does this by default; Anthropic and OpenAI offer it too). For an agent platform that sends the same system prompt and tool definitions on every call, this is the largest cost lever available, and it needs no infrastructure.

## Decision

- The first `ModelProvider` adapter is `OpenAICompatibleProvider`, configured per provider with base URL, key, model name and declared capabilities. LM Studio is the Phase 1 default; DeepSeek is enabled in v1 for comparison runs.
- A second adapter, `AnthropicProvider` (`switchboard.providers:anthropic`), speaks the native Messages API through the official `anthropic` SDK: system prompt, tool definitions and results, stop reasons, request id and usage are mapped onto the same port, structured output uses a forced tool with schema validation and one repair, and prompt caching is requested with explicit `cache_control` breakpoints on the tools and system prefix. It declares `json_schema: false` because it implements the forced tool-call strategy. No tenant selects it and no live call has been made, so the native tool-use comparison and Anthropic latency and cost remain unmeasured (T3.06 is blocked on an Anthropic key).
- The DeepSeek entry was verified on 2026-11-12 against its price page and chat-completions reference: `deepseek-flash` and `deepseek-v4-pro`, 1M context, tools and streaming supported, thinking on by default, and `response_format` limited to `json_object` rather than a JSON schema. The capability block therefore declares `json_schema: false`, and `extra_body: {reasoning_effort: none}` keeps the forced tool-call strategy valid (named or required tool choice is rejected in thinking mode).
- Capabilities (tools, JSON schema output, streaming, reasoning, context window) are declared in config, not probed, because small local models accept parameters they then ignore.
- Structured output uses the best strategy the declared capabilities allow (native JSON schema, forced tool call, or prompted JSON with one repair).
- Usage is normalized to input, cached input, cache write and output tokens, reading cached tokens from whichever field the provider returns and defaulting to zero. Anthropic reports the prompt as three numbers, so its adapter sums them into `input_tokens` and keeps the read and write parts on their own fields; a write is billed above the input rate, so the price row carries a separate write rate.
- Prompt builders follow a fixed layout: static system prompt, sorted tool definitions and static instructions first; tenant facts, history, query and evidence last. A unit test asserts byte-identical prefixes across different queries (design 8.5).
- A `MockProvider` replays recorded fixtures for CI.
- A provider may name one `fallback_provider`. A `FallbackProvider` decorator retries transient transport failures within the configured budget and then calls the fallback; non-retryable request errors fail fast. Agents and the orchestrator see one `ModelProvider` (design 8.4).

## Alternatives

- **LangChain chat model classes.** Already handle many providers. Rejected per ADR 0002; the platform needs its own normalization of tool calls, usage and errors to feed cost accounting and contract tests.
- **LiteLLM as a gateway.** Broad provider coverage. Rejected for Phase 1 as an extra dependency and process; it could become a second adapter later if many providers are wanted.
- **Native Anthropic adapter first.** The author has no key for it now. Planned for v2 to show native tool use and explicit cache control alongside the OpenAI-compatible path. It shipped in v2 (T3.06) without a live run, so it is the second adapter rather than the first.
- **Ignore prefix caching, rely on the semantic cache for cost.** Rejected: the semantic cache helps only repeated questions; prefix caching helps every call.

## Consequences

- One adapter covers two providers; the comparison report is apples to apples.
- Eval runs against a paid provider print an estimate and require confirmation; a cap in config refuses larger runs.
- Local model results are expected to be weak on tool calling; the report says so and names the model.
- Reports show the cached share of input tokens per provider; on LM Studio it is zero by construction.
- Prompt changes must respect the layout; putting a timestamp in the system prompt would silently disable caching, and the prefix test catches it.
