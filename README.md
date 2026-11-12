# Switchboard

**A source-available multi-agent platform where every routing decision is traced, every tool call is read-only and audited, and every number on the README was measured.**

Switchboard takes a user question, lets a supervisor decide whether to answer directly, search documents, query a business database through MCP tools, or do several of these in parallel, and then shows you exactly what happened and what it cost. Adding a new agent, tool, model provider, retriever, cache strategy or evaluation suite means implementing one interface and registering it, not changing core code.

[![CI](https://github.com/z27833009/switchboard/actions/workflows/ci.yml/badge.svg)](https://github.com/z27833009/switchboard/actions/workflows/ci.yml)

> Status: **Phase 1 in progress**. The evaluation harness has measured reports on LM Studio. Every metric mentioned in the docs is a target until a report backs it, and the status column in the requirements links the measured ones.

## Measured results

Dataset v1, qwen3-8b on LM Studio, full default split with no limit, run on 2026-11-12. The routing suite scored 39 of 39 test cases and the toolcall suite 40 of 40 cases.

| Suite | Result | Latency |
| --- | --- | --- |
| Routing (plan graph) | Plan accuracy 0.5385, below the 0.75 local target (Jaccard 0.6667, ambiguous cases 0.6000) | p50 2353 ms, p95 2991 ms |
| Tool call | Tool name and argument accuracy both 0.9500, above the 0.60 local target; 0.95 calls per case, no redundant calls | p50 3407.5 ms, p95 3747.8 ms |

Reports: [routing](evals/reports/routing-lmstudio.md), [tool call](evals/reports/toolcall-lmstudio.md). Latency is one model call per case, not an end-to-end run. Cost is not in USD: local models have no API cost, and no price table exists until v1. The mock reports under [evals/reports/](evals/reports/) replay recorded model output, so the CI gate measures the pipeline, not the model.

## Why this exists

Most "agent platform" demos hide three things: how the router decided, how often it was wrong, and what the run cost. Switchboard is built around making those three visible:

- A supervisor with a structured routing decision (`direct`, `rag`, `sql`, `parallel`) and a quality gate that can re-route a weak answer once.
- An MCP tool server over a public e-commerce dataset (Olist), exposing parameterized, read-only tools with a per-tenant permission model.
- A model layer that treats every OpenAI-compatible endpoint (LM Studio locally, DeepSeek in the cloud) the same way, so providers can be compared on the same test set.
- OpenTelemetry instrumentation from day one, stored in PostgreSQL and rendered in a trace view.
- A versioned evaluation harness with a regression gate in CI.

## Documents

| Document | Purpose |
| --- | --- |
| [docs/spec/requirements.md](docs/spec/requirements.md) | Goals, non-goals, user stories with acceptance criteria, quality metrics with targets |
| [docs/spec/design.md](docs/spec/design.md) | Architecture, ports and adapters, diagrams, data model, error handling, evaluation design |
| [docs/spec/tasks.md](docs/spec/tasks.md) | Phased roadmap: Phase 1 (smallest impressive demo), v1, v2 |
| [docs/spec/review.md](docs/spec/review.md) | Critical self-review: risks, over-engineering, what to cut |
| [docs/adr/](docs/adr/) | Architecture decision records |

## Planned stack

Python 3.12 with uv, FastAPI, LangGraph (as one orchestrator adapter), MCP (FastMCP), PostgreSQL 16 with pgvector, OpenTelemetry, TypeScript and React for the web UI, Docker Compose. Cloud deployment (Azure, Terraform) is an optional later phase.

## Quick start (target, not yet available)

```bash
git clone https://github.com/z27833009/switchboard
cd switchboard
cp .env.example .env
docker compose up
```

## Continuous integration

CI runs lint, type checks, unit and contract tests, the integration tests against a PostgreSQL 16 service and a locally started Olist MCP server, and the routing and toolcall eval suites on the `mock` provider. The mock provider replays recorded model output, so the eval gate measures the pipeline (parsing, plan validation, tool normalization, scoring), not the model. A prompt change alters the fixture keys, which forces a re-record against a real model and produces a new report. The gate compares against the committed files in [evals/baselines/](evals/baselines/) and fails on a drop larger than 0.05; baselines are updated by committing a new file. See [ADR 0008](docs/adr/0008-evaluation-strategy.md).

## License

Switchboard is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, modify and share it for noncommercial purposes with attribution to the author, Jiajun (GitHub: z27833009). Commercial use requires a separate license from the author.

The full Olist dataset is licensed CC BY-NC-SA 4.0 by its authors and is not redistributed here. A 1000-order subset for tests lives in [data/olist/sample/](data/olist/sample/) under the same license, with attribution.
