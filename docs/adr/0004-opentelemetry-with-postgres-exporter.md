# ADR 0004: OpenTelemetry API with a PostgreSQL exporter

Status: proposed, 2026-11-11

## Context

Every routing decision, model call and tool call must be visible with timings, tokens and cost, and the trace must survive an orchestrator swap. Options were a custom event schema, a hosted LLM tracing product, or OpenTelemetry.

## Decision

Instrument with the OpenTelemetry API from day one, following the GenAI semantic conventions for model and tool spans and a `switchboard.*` namespace for routing, cache and gate attributes. Export through a custom `PostgresSpanExporter` in Phase 1 so there is no collector to run. The MCP server propagates trace context from request metadata and exports to the same table. v1 adds an OTLP exporter and a Jaeger container behind a compose profile to show the same spans in a standard tool.

## Alternatives

- **Custom event log plus own schema.** Fewer dependencies. Rejected because it would be replaced as soon as a standard backend is wanted, and because interviewers recognize OTel.
- **Langfuse or LangSmith.** Rich UI out of the box. Rejected for the core path because the numbers on the README should come from the platform's own data, and because a hosted dependency conflicts with local-first. Langfuse self-hosted remains a possible v2 exporter target.
- **`langgraph-visualizer` as the trace path.** It is a LangChain callback handler, so it would tie tracing to the LangGraph adapter. Kept as an optional development tool instead.

## Consequences

- Span creation is part of the definition of done for any boundary call.
- Cost accounting rides on spans (token usage attributes plus a price table).
- The trace UI reads from PostgreSQL through the API, so it works with any exporter configuration.
