# ADR 0004: OpenTelemetry API with a PostgreSQL exporter

Status: proposed, 2026-11-11. Amended 2026-11-12: read-after-run consistency and step events. Amended 2026-11-13: the v1 OTLP exporter and Jaeger profile are built.

## Context

Every planning decision, model call and tool call must be visible with timings, tokens and cost, and the trace must survive an orchestrator swap. Options were a custom event schema, a hosted LLM tracing product, or OpenTelemetry.

## Decision

- Instrument with the OpenTelemetry API from day one, following the GenAI semantic conventions for model and tool spans and a `switchboard.*` namespace for plan, cache, gate and context attributes.
- Export through a custom `PostgresSpanExporter` in Phase 1 so there is no collector to run. The MCP server continues the run's trace from the `traceparent` header and exports to the same table. v1 adds an OTLP exporter and a Jaeger container behind a compose profile to show the same spans in a standard tool.
- **Exporter switch.** `tracing.exporter` in `platform.yaml` is `postgres` (default), `otlp` or `none`, resolved by both the API and the MCP server in `switchboard/tracing/config.py`. `otlp` uses the official OTLP/HTTP exporter with `OTEL_EXPORTER_OTLP_ENDPOINT`; the compose `tracing` profile supplies Jaeger and points both containers at `http://jaeger:4318`. The exporter choice replaces the PostgreSQL exporter rather than adding to it, so while OTLP is selected the platform trace endpoint is empty and Jaeger is the trace view. The default never changes silently, and a missing collector does not prevent startup: the batch processor exports in the background.
- **Read-after-run consistency.** Batch export is asynchronous, so a trace read right after a run could be incomplete. The run row is inserted when the run starts; `spans.run_id` has an index but no foreign key; the API calls `force_flush()` before returning; the MCP server flushes at the end of each call; a flush timeout marks the trace `incomplete` rather than returning a silently partial tree.
- **Step events.** A `StepEventProcessor` (an OTel span processor) publishes selected span starts and ends to a per-run queue that feeds the `/chat/stream` SSE endpoint. Live progress and the trace come from one instrumentation.

## Alternatives

- **Custom event log plus own schema.** Fewer dependencies. Rejected because it would be replaced as soon as a standard backend is wanted, and because interviewers recognize OTel.
- **Langfuse or LangSmith.** Rich UI out of the box. Rejected for the core path because the numbers on the README should come from the platform's own data, and because a hosted dependency conflicts with local-first. Langfuse self-hosted remains a possible v2 exporter target.
- **`langgraph-visualizer` as the trace path.** It is a LangChain callback handler, so it would tie tracing to the LangGraph adapter. Kept as an optional development tool instead.
- **A separate event bus for streaming.** Rejected: two instrumentations of the same steps would drift apart.
- **Synchronous `SimpleSpanProcessor` in the API.** Removes the race but adds a database write to every span on the request path. Kept for the MCP server only, where calls are few.

## Consequences

- Span creation is part of the definition of done for any boundary call.
- Cost accounting rides on spans (token usage attributes including cached input tokens, plus a price table).
- The trace UI reads from PostgreSQL through the API, so it works with the default exporter. While the OTLP exporter is selected the trace UI is empty by design and Jaeger is the trace view (T2.13).
- `/chat` latency includes one flush (bounded at 2 seconds), which is visible in the `run` span.
