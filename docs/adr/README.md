# Architecture Decision Records

One short record per decision that an interviewer might ask "why" about. Format: context, decision, alternatives considered, consequences. Status is `proposed` until the related phase ships, then `accepted`; superseded records stay in place with a pointer. While a record is still `proposed`, it may be amended in place with a dated note in its status line.

| ID | Title | Status |
| --- | --- | --- |
| [0001](0001-modular-monolith-with-ports-and-adapters.md) | Modular monolith with ports and adapters | proposed |
| [0002](0002-langgraph-behind-an-orchestrator-port.md) | LangGraph behind an orchestrator port, with an agent loop as the second adapter | proposed, amended 2026-11-12 |
| [0003](0003-postgresql-for-vectors-cache-and-traces.md) | PostgreSQL for vectors, cache and traces; no Redis in Phase 1 | proposed |
| [0004](0004-opentelemetry-with-postgres-exporter.md) | OpenTelemetry API with a PostgreSQL exporter | proposed, amended 2026-11-12 |
| [0005](0005-mcp-tools-read-only-parameterized.md) | MCP tool server in Python with read-only parameterized tools | proposed, amended 2026-11-12 |
| [0006](0006-openai-compatible-provider-first.md) | OpenAI-compatible adapter as the first model provider, local models first | proposed, amended 2026-11-12 |
| [0007](0007-semantic-cache-scoped-by-route-and-tenant.md) | Semantic cache before routing, scoped by tenant and index version | proposed, amended 2026-11-12 |
| [0008](0008-evaluation-strategy.md) | Evaluation strategy: versioned datasets, mock provider in CI, honest reporting | proposed, amended 2026-11-12 |
| [0009](0009-license-polyform-noncommercial.md) | License: PolyForm Noncommercial 1.0.0 | proposed |
| [0010](0010-demo-domain-olist-and-synthetic-documents.md) | Demo domain: Olist dataset plus synthetic policy documents | proposed |
| [0011](0011-routing-as-a-plan-over-registered-agents.md) | Routing as a plan over registered agents | proposed |
| [0012](0012-bounded-tool-output-and-layered-context-policy.md) | Bounded tool output and a layered context policy | proposed |
| [0013](0013-quality-gate-deterministic-checks-first.md) | Quality gate with deterministic checks first and one corrective action | proposed |
| [0014](0014-signed-run-tokens-between-api-and-tool-server.md) | Signed run tokens between the API and the tool server | proposed |
