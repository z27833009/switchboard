# Architecture Decision Records

One short record per decision that an interviewer might ask "why" about. Format: context, decision, alternatives considered, consequences. Status is `proposed` until the related phase ships, then `accepted`; superseded records stay in place with a pointer. While a record is still `proposed`, it may be amended in place with a dated note in its status line.

Status was reconciled with the code on 2026-11-13. Every record is `accepted`; ADR 0007's measured caution is that the semantic cache false-hit rate is above target, so the demo tenants keep it disabled.

| ID | Title | Status |
| --- | --- | --- |
| [0001](0001-modular-monolith-with-ports-and-adapters.md) | Modular monolith with ports and adapters | accepted, 2026-11-13 |
| [0002](0002-langgraph-behind-an-orchestrator-port.md) | LangGraph behind an orchestrator port, with an agent loop as the second adapter | accepted, 2026-11-13, amended 2026-11-12 |
| [0003](0003-postgresql-for-vectors-cache-and-traces.md) | PostgreSQL for vectors, cache and traces; no Redis in Phase 1 | accepted, 2026-11-13 |
| [0004](0004-opentelemetry-with-postgres-exporter.md) | OpenTelemetry API with a PostgreSQL exporter | accepted, 2026-11-13, amended 2026-11-12 and 2026-11-13 |
| [0005](0005-mcp-tools-read-only-parameterized.md) | MCP tool server in Python with read-only parameterized tools | accepted, 2026-11-13, amended 2026-11-12 |
| [0006](0006-openai-compatible-provider-first.md) | OpenAI-compatible adapter as the first model provider, local models first | accepted, 2026-11-13, amended 2026-11-13 (DeepSeek measured) |
| [0007](0007-semantic-cache-scoped-by-route-and-tenant.md) | Semantic cache before routing, scoped by tenant and index version | accepted, 2026-11-13, amended 2026-11-12 (measured false-hit above target, demo off) |
| [0008](0008-evaluation-strategy.md) | Evaluation strategy: versioned datasets, mock provider in CI, honest reporting | accepted, 2026-11-13, amended 2026-11-12 |
| [0009](0009-license-polyform-noncommercial.md) | License: PolyForm Noncommercial 1.0.0 | accepted, 2026-11-13 |
| [0010](0010-demo-domain-olist-and-synthetic-documents.md) | Demo domain: Olist dataset plus synthetic policy documents | accepted, 2026-11-13 |
| [0011](0011-routing-as-a-plan-over-registered-agents.md) | Routing as a plan over registered agents | accepted, 2026-11-13 |
| [0012](0012-bounded-tool-output-and-layered-context-policy.md) | Bounded tool output and a layered context policy | accepted, 2026-11-13 |
| [0013](0013-quality-gate-deterministic-checks-first.md) | Quality gate with deterministic checks first and one corrective action | accepted, 2026-11-13 (layer 1) |
| [0014](0014-signed-run-tokens-between-api-and-tool-server.md) | Signed run tokens between the API and the tool server | accepted, 2026-11-13 |
