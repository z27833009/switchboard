# Switchboard Requirements

Version 0.1, 2026-11-11. Status: draft for review.

## 1. Purpose

Switchboard is a multi-agent platform that routes user questions to the right answering strategy (direct answer, document retrieval, database tools, or several in parallel), executes it, and makes every decision observable and measurable. It is built as a portfolio project by a solo developer and must therefore be small enough to finish and clear enough to explain in an interview.

## 2. Goals

| ID | Goal | How we know it is met |
| --- | --- | --- |
| G1 | Demonstrate architecture thinking | Clear boundaries (ports and adapters), documented decisions (ADRs), diagrams an interviewer can follow in five minutes |
| G2 | Extensibility without core changes | Adding an agent, tool, model provider, retriever, cache strategy or eval suite is one class plus one registry entry (see AC-07, AC-08) |
| G3 | Honest, measured numbers | Routing accuracy, tool-call accuracy, cache hit and false-hit rate, latency and cost per provider are produced by a reproducible harness and stored with the dataset version |
| G4 | Demoable in five minutes, runnable with one command | `docker compose up` brings up the full local stack; a scripted demo covers routing, tools, trace and eval |
| G5 | Interview preparation | Every component has a short "why" in the design and an ADR; the review document lists the trade-offs |

## 3. Non-goals

- Not a general chat product. No user accounts, no conversation history beyond one session in Phase 1.
- Not a hosted service. Cloud deployment is optional and comes last.
- Not a benchmark of frontier models. Provider comparison covers what the developer can run locally (LM Studio) plus one paid API (DeepSeek).
- Not physical tenant isolation. Multi-tenancy is logical (tenant id on every record and request) with per-tenant configuration and tool permissions.
- Not a replacement for LangSmith or Langfuse. The trace UI shows routing decisions and tool calls for this platform only.
- No write tools in Phase 1 and v1. Tools are read-only by design; write tools are a v2 topic behind human approval.
- No reuse of any code, prompt, configuration or data from the author's former employer. Public datasets and self-written synthetic documents only.

## 4. Personas

| Persona | Needs |
| --- | --- |
| Interviewer | Wants to see a working demo, a trace, the numbers and the design rationale in a few minutes |
| Platform developer (the author, or a contributor) | Wants to add a tool, provider or agent without touching the orchestrator |
| Evaluator | Wants to run the suites against a provider and get a comparable report |
| Demo end user | Asks questions about orders, sellers, products and shop policies for the demo shop |

## 5. User stories and acceptance criteria

Each story has an ID, a phase and acceptance criteria (AC) that are testable. Phase 1 is the smallest impressive demo, v1 and v2 follow in [tasks.md](tasks.md).

### US-01 Ask a question and see the route (Phase 1)

As a demo user I ask a question and get an answer together with the route that produced it.

- AC-01a `POST /chat` with `{"query": "...", "tenant_id": "default"}` returns `{"run_id", "answer", "route", "citations", "usage"}` within the configured timeout.
- AC-01b The route is one of `direct`, `rag`, `sql`, `parallel` and is persisted on the run.
- AC-01c A routing decision includes a `reason` string and a `confidence` number between 0 and 1.

### US-02 Database question answered through MCP tools (Phase 1)

As a demo user I ask "How many orders were delivered late in March 2018?" and get an answer computed from the database.

- AC-02a The SQL agent calls at least one MCP tool; the tool name and arguments appear in the trace.
- AC-02b All tools exposed by the MCP server are parameterized and read-only. A test proves that a tool cannot execute `INSERT`, `UPDATE`, `DELETE` or DDL (database role has SELECT only and transactions are read-only).
- AC-02c Every tool call is written to the audit table with tenant id, tool name, tool version, argument hash, status and duration.

### US-03 Document question answered through RAG with citations (Phase 1)

As a demo user I ask "What is the return window for electronics?" and get an answer that cites the policy document.

- AC-03a The RAG agent returns at least one citation with document id, chunk id and a text snippet.
- AC-03b Documents are ingested by a CLI command (`switchboard ingest <path>`) that chunks, embeds and stores them for a tenant.
- AC-03c Re-ingesting an unchanged document is a no-op (content hash check).

### US-04 Parallel route with merge (Phase 1)

As a demo user I ask a question that needs both data and policy ("Were late deliveries in March eligible for a refund?") and get one merged answer.

- AC-04a The supervisor selects `parallel`; the SQL and RAG agents run concurrently (trace shows overlapping spans).
- AC-04b A merge step produces a single answer that references both sources.
- AC-04c If one branch fails, the merged answer states which part is missing instead of failing the whole run.

### US-05 Inspect the trace of a run (Phase 1)

As an interviewer I open a run and see the tree of routing decision, agent runs, model calls and tool calls with timings and tokens.

- AC-05a `GET /runs/{id}/trace` returns a span tree where every span has name, kind, start, end, status and attributes.
- AC-05b Model call spans carry `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` following OpenTelemetry GenAI semantic conventions.
- AC-05c The web UI renders the tree with expand and collapse and shows a span's attributes on selection.

### US-06 Run an evaluation suite and get a report (Phase 1)

As an evaluator I run `switchboard eval run --suite routing --provider lmstudio` and get a report.

- AC-06a The harness loads a versioned dataset (`evals/datasets/v1/routing.jsonl`), runs every case and writes `evals/reports/<timestamp>-routing-<provider>.md` and `.json`.
- AC-06b The report states dataset version, provider, model, number of cases, accuracy, p50 and p95 latency and total cost.
- AC-06c The same command with `--provider mock` runs without network access and finishes in under 30 seconds.

### US-07 Add a new tool without core changes (Phase 1)

As a platform developer I add a tool to the MCP server by writing one function with a schema and registering it.

- AC-07a Adding a tool touches only files under `mcp_servers/` plus the tool manifest; no file under `src/switchboard/core` or `src/switchboard/adapters/orchestrator` changes. Verified by a documented walkthrough and a PR diff.
- AC-07b The new tool appears in `list_tools` for tenants that allow it and is callable by the SQL agent without code changes.

### US-08 Add a new model provider without core changes (Phase 1 contract, second implementation in v1)

- AC-08a A new provider implements `ModelProvider` and is registered in `config/providers.yaml`; the orchestrator and agents do not change.
- AC-08b A shared contract test suite (`tests/contracts/`) runs against every registered provider adapter and covers: plain completion, structured output, tool call with one tool, tool call with no matching tool, and error mapping.

### US-09 Quality gate re-routes a weak answer (v1)

- AC-09a After an agent answers, a gate scores groundedness and completeness; if below threshold the supervisor re-routes once along a documented escalation path (`direct` to `rag`, `rag` or `sql` to `parallel`).
- AC-09b At most one re-route per run; the trace shows both attempts and the gate verdicts.
- AC-09c A test set of 30 deliberately weak cases measures gate precision and recall; numbers are reported, not assumed.

### US-10 Semantic cache (v1)

- AC-10a Only routes marked cacheable (`direct`, `rag`) are looked up; `sql` and `parallel` are never cached.
- AC-10b A cache entry is scoped by tenant, route and document index version; re-ingesting documents for a tenant invalidates that tenant's entries.
- AC-10c A cache eval suite with paraphrase pairs and near-miss pairs reports hit rate and false-hit rate; the similarity threshold is chosen from this report and documented.
- AC-10d A cache hit is visible in the trace and in the run record, and the run cost for a hit is the embedding cost only.

### US-11 Tenant configuration and tool permissions (v1)

- AC-11a A tenant is defined in `config/tenants.yaml` with allowed tools, default provider, retrieval settings and cache settings.
- AC-11b The MCP server enforces the tool allowlist server-side; a denied call returns a permission error that is visible in the trace and the audit log.
- AC-11c Tenant-scoped tools automatically filter rows by the tenant's seller ids; a test proves that tenant A cannot see tenant B's orders.

### US-12 Prompt injection in tool and document content (v1)

- AC-12a Tool results and retrieved chunks are passed to the model as data inside delimited blocks with an explicit instruction that they are not commands.
- AC-12b An injection detector flags content matching a documented pattern set; flagged content is marked in the trace and the agent is told it was flagged.
- AC-12c An injection test set (at least 20 malicious and 20 benign samples) reports detection rate and false positive rate.

### US-13 Provider comparison report (v1)

- AC-13a `switchboard eval compare --providers lmstudio,deepseek --suites routing,toolcall` produces one markdown table with accuracy, p50 latency, p95 latency and cost per 100 queries per provider.
- AC-13b Cost is computed from a versioned price table; local models report zero API cost and the report says so explicitly.

### US-14 Regression gate in CI (Phase 1 smoke, v1 full)

- AC-14a CI runs lint, type check, unit tests, integration tests against a PostgreSQL service and the eval smoke suite with the mock provider.
- AC-14b The smoke suite compares against `evals/baselines/<suite>.json` and fails the build if accuracy drops by more than a configured delta.

### US-15 Human-in-the-loop hook (v2)

- AC-15a The orchestrator exposes an interrupt point before any tool with risk level `write` or any tool listed in the tenant's `require_approval` list.
- AC-15b A run waiting for approval has status `waiting_approval` and can be resumed or rejected through the API.

### US-16 Replace the orchestrator (v2)

- AC-16a A second `Orchestrator` adapter (a sequential, framework-free implementation) passes the same orchestrator contract tests as the LangGraph adapter.
- AC-16b Switching is a config change; the routing eval suite produces comparable results on both.

### US-17 One command local run (Phase 1)

- AC-17a `docker compose up` starts PostgreSQL, the API, the MCP server and the web UI; `GET /health` reports every dependency.
- AC-17b A fresh clone with LM Studio running on the host reaches a first answer in under 15 minutes following the README, including dataset load.

### US-18 Audit log (v1)

- AC-18a `GET /audit?tenant_id=...` lists tool calls with timestamps, tool, version, argument hash and outcome.
- AC-18b Arguments are stored redacted according to the tool manifest's `sensitive_args` list.

### US-19 Cost and token accounting (v1)

- AC-19a Every run stores input tokens, output tokens and cost in USD computed from the price table.
- AC-19b `GET /runs?tenant_id=...` supports aggregating cost per day and per provider.

### US-20 Cloud deployment (v2, optional)

- AC-20a Terraform creates the Azure resources (container apps, PostgreSQL flexible server, key vault) and a GitHub Actions workflow deploys a tagged image.

## 6. Quality metrics

All values in the Target column are targets. The Status column says `target` until a report under `evals/reports/` backs the number, after which the measured value and the report path replace it. No number is moved to the README without a report.

| Metric | Definition | Measured by | Target | Status |
| --- | --- | --- | --- | --- |
| Routing accuracy | Share of cases where the chosen route equals the labeled route | `routing` suite, dataset v1 | >= 0.90 with a cloud model, >= 0.75 with a 7B local model | target |
| Tool-call accuracy | Share of cases where the tool name and all required arguments match the label (AST match, BFCL style) | `toolcall` suite | >= 0.85 cloud, >= 0.60 local | target |
| RAG recall@5 | Share of cases where a labeled relevant chunk is in the top 5 | `rag` suite (v1) | >= 0.85 hybrid, measured against vector-only | target |
| Gate precision and recall | Weak answers correctly rejected, good answers not rejected | `gate` suite (v1) | precision >= 0.80, recall >= 0.70 | target |
| Cache hit rate | Hits divided by lookups on the cache suite | `cache` suite (v1) | reported, no target (depends on query mix) | target |
| Cache false-hit rate | Hits where the labeled answer differs | `cache` suite (v1) | <= 0.02 | target |
| Latency p50 and p95 per route | Wall time from request to answer | every suite | reported per provider; p50 under 8 s for `parallel` with a cloud model | target |
| Cost per 100 queries | Sum of model cost from the price table | every suite | reported per provider | target |
| Injection detection rate | Malicious samples flagged | `injection` suite (v1) | >= 0.90 detection, <= 0.05 false positives | target |
| Local startup time | Fresh clone to first answer | manual, documented | < 15 min | target |

## 7. Constraints

- Solo developer, evenings. Phase 1 must be reachable in roughly 30 to 35 hours of work.
- Clean-room: no artifacts from any employer. See ADR 0010 for the demo dataset.
- Local-first: everything runs on a Windows 11 PC with Docker Desktop and LM Studio; no cloud dependency for Phase 1.
- Paid API usage (DeepSeek) is reserved for v1 comparison runs; every eval run prints its estimated cost before starting and refuses to run above a configured cap.
- Documents and code are in English; no em dashes in any text.
- License: PolyForm Noncommercial 1.0.0 (ADR 0009).

## 8. Glossary

| Term | Meaning |
| --- | --- |
| Route | The strategy the supervisor picks for a request: `direct`, `rag`, `sql` or `parallel` |
| Agent | A component that fulfills one route and returns an `AgentResult` |
| Port | An interface defined in `core` that the rest of the system depends on |
| Adapter | A concrete implementation of a port (for example the LangGraph orchestrator) |
| Tool source | Anything that can list and call tools; the first implementation is an MCP client |
| Run | One request through the platform, with its trace, answer and cost |
| Tenant | A logical customer of the platform with its own config, documents and tool permissions |
| Quality gate | A check after an agent answers that can trigger one re-route |
| Index version | A per-tenant counter bumped on every ingestion; used for cache invalidation |
