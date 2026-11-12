# Switchboard Requirements

Version 0.2, 2026-11-12. Status: draft for review. Changes since 0.1 are listed in [review.md](review.md) section 0.

## 1. Purpose

Switchboard is a multi-agent platform that answers user questions by choosing a plan (a direct answer, document retrieval, database tools, or several agents in parallel), executing it, and making every decision observable and measurable. Every number in an answer can be traced to the tool call that produced it. It is built as a portfolio project by a solo developer and must therefore be small enough to finish and clear enough to explain in an interview.

## 2. Goals

| ID | Goal | How we know it is met |
| --- | --- | --- |
| G1 | Demonstrate architecture thinking | Clear boundaries (ports and adapters), documented decisions (ADRs), diagrams an interviewer can follow in five minutes |
| G2 | Extensibility without core changes | Adding an agent, tool, model provider, retriever, cache strategy or eval suite is one class plus one registry entry (see AC-07, AC-08, AC-23c) |
| G3 | Honest, measured numbers | Plan accuracy, tool-call accuracy, retrieval recall, cache hit and false-hit rate, latency and cost per provider and per orchestrator are produced by a reproducible harness and stored with the dataset version |
| G4 | Demoable in five minutes, runnable with one command | `docker compose up` brings up the full local stack; a scripted demo covers planning, tools, citations, live steps, trace and eval |
| G5 | Interview preparation | Every component has a short "why" in the design and an ADR; the review document lists the trade-offs, including a measured comparison of the routing design against a single agent loop |

## 3. Non-goals

- Not a general chat product. No user accounts and no long-term memory. Phase 1 is single-turn; v1 adds minimal in-session follow-ups (the last few turns of one session).
- No end-user authentication. The `tenant_id` on `/chat` is trusted for the demo. The boundary between the API and the tool server is authenticated (ADR 0014).
- Not a hosted service. Cloud deployment is optional and comes last.
- Not a benchmark of frontier models. Provider comparison covers what the developer can run locally (LM Studio) plus one paid API (DeepSeek).
- Not physical tenant isolation. Multi-tenancy is logical (tenant id on every record and request) with per-tenant configuration and tool permissions.
- Not a replacement for LangSmith or Langfuse. The trace UI shows planning decisions and tool calls for this platform only.
- No write tools in Phase 1 and v1. Tools are read-only by design; write tools are a v2 topic behind human approval.
- No code execution tools, and therefore no sandbox (design 7.6).
- No model-based context summarization in Phase 1 or v1 (ADR 0012).
- No reuse of any code, prompt, configuration or data from the author's former employer. Public datasets and self-written synthetic documents only.

## 4. Personas

| Persona | Needs |
| --- | --- |
| Interviewer | Wants to see a working demo, a trace, the numbers and the design rationale in a few minutes |
| Platform developer (the author, or a contributor) | Wants to add a tool, provider or agent without touching the orchestrator |
| Evaluator | Wants to run the suites against a provider or an orchestrator and get a comparable report |
| Demo end user | Asks questions about orders, sellers, products and shop policies for the demo shop |

## 5. User stories and acceptance criteria

Each story has an ID, a phase and acceptance criteria (AC) that are testable. Phase 1 is the smallest impressive demo, v1 and v2 follow in [tasks.md](tasks.md).

### US-01 Ask a question and see the plan (Phase 1)

As a demo user I ask a question and get an answer together with the plan that produced it.

- AC-01a `POST /chat` with `{"query": "...", "tenant_id": "default"}` returns `{"run_id", "answer", "plan", "citations", "usage"}` within the configured timeout.
- AC-01b The plan is a non-empty list of registered agent names that the tenant may use; its label (for example `data+docs`) is persisted on the run.
- AC-01c A plan includes a `reason` string. It carries no self-reported confidence (design 4).
- AC-01d An invalid plan (unknown agent, exclusive agent combined with others, too many agents) is repaired once and otherwise replaced by the configured fallback plan; the trace records which happened.

### US-02 Database question answered through MCP tools (Phase 1)

As a demo user I ask "How many orders were delivered late in March 2018?" and get an answer computed from the database.

- AC-02a The data agent calls at least one MCP tool; the tool name and arguments appear in the trace.
- AC-02b All tools exposed by the MCP server are parameterized and read-only. A test proves that a tool cannot execute `INSERT`, `UPDATE`, `DELETE` or DDL (database role has SELECT only and transactions are read-only).
- AC-02c Every tool call is written to the audit table with tenant id, tool name, tool version, argument hash, status and duration.
- AC-02d Every number in the answer that comes from a tool result carries a tool citation linking to the tool span and the audit row (data citations).

### US-03 Document question answered through RAG with citations (Phase 1)

As a demo user I ask "What is the return window for electronics?" and get an answer that cites the policy document.

- AC-03a The docs agent returns at least one citation with document id, chunk id and a text snippet.
- AC-03b Documents are ingested by a CLI command (`switchboard ingest <path>`) that chunks, embeds and stores them for a tenant.
- AC-03c Re-ingesting an unchanged document is a no-op (content hash check).

### US-04 Multi-agent plan with merge (Phase 1)

As a demo user I ask a question that needs both data and policy ("Were late deliveries in March eligible for compensation?") and get one merged answer.

- AC-04a The supervisor selects a plan with both `data` and `docs`; the agents run concurrently (trace shows overlapping spans).
- AC-04b A merge step produces a single answer that keeps the citations of both sources.
- AC-04c If one branch fails, the merged answer states which part is missing instead of failing the whole run.

### US-05 Inspect the trace of a run (Phase 1)

As an interviewer I open a run and see the tree of planning decision, agent runs, model calls and tool calls with timings and tokens.

- AC-05a `GET /runs/{id}/trace` returns a span tree where every span has name, kind, start, end, status and attributes.
- AC-05b Model call spans carry `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` following OpenTelemetry GenAI semantic conventions, plus cached input tokens.
- AC-05c The web UI renders the tree with expand and collapse and shows a span's attributes on selection; a citation chip in the answer opens the cited span.
- AC-05d A trace requested immediately after `/chat` returns is complete, including spans from the MCP server; if flushing timed out the trace is marked `incomplete` (design 12.2).

### US-06 Run an evaluation suite and get a report (Phase 1)

As an evaluator I run `switchboard eval run --suite routing --provider lmstudio` and get a report.

- AC-06a The harness loads a versioned dataset (`evals/datasets/v1/routing.jsonl`), runs every case and writes `evals/reports/<timestamp>-routing-<provider>.md` and `.json`.
- AC-06b The report states dataset version, orchestrator, provider, model, number of cases, accuracy, p50 and p95 latency, input, cached input and output tokens, and total cost.
- AC-06c The same command with `--provider mock` runs without network access and finishes in under 30 seconds.

### US-07 Add a new tool without core changes (Phase 1)

As a platform developer I add a tool to the MCP server by writing one function with a schema and registering it.

- AC-07a Adding a tool touches only files under `mcp_servers/` plus the tool manifest; no file under `src/switchboard/core` or `src/switchboard/adapters/orchestrator` changes. Verified by a documented walkthrough and a PR diff.
- AC-07b The new tool appears in `list_tools` for tenants that allow it and is callable by the data agent without code changes.

### US-08 Add a new model provider without core changes (Phase 1 contract, second implementation in v1)

- AC-08a A new provider implements `ModelProvider` and is registered in `config/providers.yaml`; the orchestrator and agents do not change.
- AC-08b A shared contract test suite (`tests/contracts/`) runs against every registered provider adapter and covers: plain completion, structured output, tool call with one tool, tool call with no matching tool, usage normalization including cached input tokens, and error mapping.

### US-09 Quality gate with corrective action (v1)

- AC-09a After an agent or merge answers, deterministic checks run: numbers grounded in evidence, citations valid, evidence present, merge complete (design 6.8).
- AC-09b A failed grounding or citation check retries the same agent once with concrete feedback; missing evidence triggers one re-plan with a different plan. At most one corrective action per run; the trace shows both attempts and the failed checks.
- AC-09c A gate test set of 30 cases measures precision and recall per check; numbers are reported, not assumed.

### US-10 Semantic cache (v1)

- AC-10a The lookup happens before the supervisor. Only answers from cacheable plans (every agent cacheable) are stored; plans containing `data` are never stored. Lookups are skipped when the session has history.
- AC-10b A cache entry is scoped by tenant and document index version; re-ingesting documents for a tenant invalidates that tenant's entries.
- AC-10c A cache eval suite with paraphrase pairs and near-miss pairs (including pairs where one query needs data) reports hit rate and false-hit rate; the similarity threshold is chosen from this report and documented.
- AC-10d A cache hit is visible in the trace and in the run record, makes no model call, and costs the query embedding only.

### US-11 Tenant configuration and tool permissions (v1)

- AC-11a A tenant is defined in `config/tenants.yaml` with allowed agents, allowed tools, default provider, retrieval settings and cache settings.
- AC-11b The MCP server takes the tenant from a signed run token and enforces the tool allowlist server-side; a denied call returns a permission error that is visible in the trace and the audit log.
- AC-11c Tenant-scoped tools automatically filter rows by the tenant's seller ids; a test proves that tenant A cannot see tenant B's orders.
- AC-11d (Phase 1) Tests prove that the tool server rejects a call with no token, a forged token and an expired token, and that a tenant id supplied in tool arguments is ignored.

### US-12 Prompt injection in tool and document content (v1)

- AC-12a Tool results and retrieved chunks are passed to the model as data inside delimited blocks with an explicit instruction that they are not commands.
- AC-12b An injection detector flags content matching a documented pattern set; flagged content is marked in the trace and the agent is told it was flagged.
- AC-12c An injection test set (at least 20 malicious and 20 benign samples) reports detection rate and false positive rate.

### US-13 Provider and architecture comparison report (v1)

- AC-13a `switchboard eval compare` produces one markdown table with plan accuracy, tool-call accuracy, p50 and p95 latency, tokens and cost per 100 queries, for each combination of provider (LM Studio, DeepSeek) and orchestrator (plan graph, agent loop).
- AC-13b Cost is computed from a versioned price table with cached input priced separately; local models report zero API cost and the report says so explicitly.

### US-14 Regression gate in CI (Phase 1 smoke, v1 full)

- AC-14a CI runs lint, type check, unit tests, integration tests against a PostgreSQL service and the eval smoke suite with the mock provider.
- AC-14b The smoke suite compares against `evals/baselines/<suite>.json` and fails the build if accuracy drops by more than a configured delta.
- AC-14c A mock provider request without a matching fixture fails loudly and names the purpose to re-record; the README states that the CI gate measures the pipeline, not the model.

### US-15 Human-in-the-loop hook (v2)

- AC-15a The orchestrator exposes an interrupt point before any tool with risk level `write` or any tool listed in the tenant's `tools.ask` list.
- AC-15b A run waiting for approval has status `waiting_approval` and can be resumed or rejected through the API.

### US-16 Agent loop orchestrator as control group (v1)

- AC-16a A second `Orchestrator` adapter runs one model with all of the tenant's tools plus `search_docs` in a bounded loop, and passes the same orchestrator contract tests as the LangGraph adapter.
- AC-16b Switching is a config change. Its plan is derived from the tools it used, so the routing suite scores both orchestrators on the same dataset; the comparison appears in the US-13 report.

### US-17 One command local run (Phase 1)

- AC-17a `docker compose up` starts PostgreSQL, the API, the MCP server and the web UI; `GET /health` reports every dependency.
- AC-17b A fresh clone with LM Studio running on the host reaches a first answer in under 15 minutes following the README, including dataset load.

### US-18 Audit log (v1)

- AC-18a `GET /audit?tenant_id=...` lists tool calls with timestamps, tool, version, argument hash and outcome.
- AC-18b Arguments are stored redacted according to the tool manifest's `sensitive_args` list.

### US-19 Cost and token accounting (v1)

- AC-19a Every run stores input, cached input and output tokens and cost in USD computed from the price table.
- AC-19b `GET /runs?tenant_id=...` supports aggregating cost per day and per provider.
- AC-19c (Phase 1) Prompts follow the prefix layout in design 8.5; a unit test asserts byte-identical prefixes across different queries.

### US-20 Cloud deployment (v2, optional)

- AC-20a Terraform creates the Azure resources (container apps, PostgreSQL flexible server, key vault) and a GitHub Actions workflow deploys a tagged image.

### US-21 Bounded tool output in the model context (Phase 1)

- AC-21a The model receives a `ToolResultView` (summary, total rows, at most `model_view_rows` rows, `truncated`, `result_id`), never more rows than the limit.
- AC-21b The platform tool `result_page@1` returns further rows of a result already fetched in the same run.
- AC-21c Before each model call the context policy clears the oldest consumed tool results when the estimated context exceeds the configured fill ratio; the span records whether and how much it cleared. No model is called to summarize.
- AC-21d The full tool result is visible in the trace (capped at 64 KB).

### US-22 Live progress (Phase 1 steps, v1 tokens)

- AC-22a `POST /chat/stream` emits server-sent events for run start, plan, agent start and finish, tool calls and run finish, generated from the same spans as the trace.
- AC-22b The chat page shows the steps as they arrive.
- AC-22c (v1) Answer text streams as `answer.delta` events when the provider supports streaming.

### US-23 Follow-up questions and clarification (v1)

- AC-23a `/chat` accepts an optional `session_id`; the last `history_turns` turns of that session are given to the supervisor and to the docs agent's query rewriting.
- AC-23b "And in April?" after a March question produces a correct data plan with April dates.
- AC-23c A `clarify` agent is added as one class plus one entry point, with no change under `core` or the orchestrator; for an ambiguous question it returns one question and the run ends with status `needs_clarification`.

### US-24 Feedback into evaluation (v1)

- AC-24a `POST /runs/{id}/feedback` stores a rating and an optional note.
- AC-24b `switchboard eval harvest` exports negatively rated runs as unlabeled candidate cases; labeled cases enter only the next dataset version.

### US-25 Conditional query rewriting (v1)

- AC-25a The docs agent rewrites the query only when the session has history or the first retrieval's top score is below the configured minimum; at most two retrieval rounds.
- AC-25b The `rag` suite reports recall@5 for query as written, always rewrite, conditional rewrite, and conditional rewrite plus hybrid search.

## 6. Quality metrics

All values in the Target column are targets. The Status column says `target` until a report under `evals/reports/` backs the number, after which the measured value and the report path replace it. No number is moved to the README without a report.

| Metric | Definition | Measured by | Target | Status |
| --- | --- | --- | --- | --- |
| Plan accuracy | Share of cases where the chosen set of agents equals the labeled set | `routing` suite, dataset v1 | >= 0.90 with a cloud model, >= 0.75 with a 7B local model | measured 0.5385 on qwen3-8b via LM Studio, dataset v1 test split, 39 of 39 cases (Jaccard 0.6667, unambiguous 0.5294, ambiguous 0.6000), below the 0.75 local target: [routing report](../../evals/reports/routing-lmstudio.md) |
| Tool-call accuracy | Share of cases where the tool name and all required arguments match the label (AST match, BFCL style) | `toolcall` suite | >= 0.85 cloud, >= 0.60 local | measured 0.9500 on qwen3-8b via LM Studio, dataset v1, 40 of 40 cases (tool name 0.9500, arguments 0.9500), above the 0.60 local target: [toolcall report](../../evals/reports/toolcall-lmstudio.md) |
| Tool calls per data case | Mean tool calls and share of redundant calls | `toolcall` suite | reported, no target | measured 0.95 calls per case and redundant call rate 0.0000 on qwen3-8b via LM Studio, dataset v1, 40 cases: [toolcall report](../../evals/reports/toolcall-lmstudio.md) |
| Architecture comparison | Plan accuracy, tool-call accuracy, latency and cost of plan graph versus agent loop | US-13 report (v1) | reported, no target (the point is to find out) | target |
| RAG recall@5 | Share of cases where a labeled relevant chunk is in the top 5 | `rag` suite (v1) | >= 0.85 for the best strategy, reported for all four | target |
| Gate precision and recall | Weak answers correctly rejected, good answers not rejected, per check | `gate` suite (v1) | precision >= 0.80, recall >= 0.70 | target |
| Cache hit rate | Hits divided by lookups on the cache suite | `cache` suite (v1) | reported, no target (depends on query mix) | target |
| Cache false-hit rate | Hits where the labeled answer differs | `cache` suite (v1) | <= 0.02 | target |
| Cached input share | Cached input tokens divided by input tokens | every suite, per provider | reported, no target | measured 0.0000 on qwen3-8b via LM Studio: 0 cached input tokens of 14,626 routing and 32,945 toolcall input tokens: [routing report](../../evals/reports/routing-lmstudio.md), [toolcall report](../../evals/reports/toolcall-lmstudio.md) |
| Latency p50 and p95 per plan | Wall time from request to answer | every suite | reported per provider; p50 under 8 s for `data+docs` with a cloud model | measured model-call latency, not end-to-end run: routing p50 2353 ms, p95 2991 ms; toolcall p50 3407.5 ms, p95 3747.8 ms on qwen3-8b via LM Studio, dataset v1: [routing report](../../evals/reports/routing-lmstudio.md), [toolcall report](../../evals/reports/toolcall-lmstudio.md). The data+docs cloud target was not tested |
| Cost per 100 queries | Sum of model cost from the price table | every suite | reported per provider | measured no API cost for the local LM Studio runs. The JSON `cost_usd` is null until the T2.06 price table, and no USD value is claimed for a paid provider: [routing report](../../evals/reports/routing-lmstudio.md), [toolcall report](../../evals/reports/toolcall-lmstudio.md) |
| Injection detection rate | Malicious samples flagged | `injection` suite (v1) | >= 0.90 detection, <= 0.05 false positives | target |
| Local startup time | Fresh clone to first answer | manual, documented | < 15 min | target |

## 7. Constraints

- Solo developer, evenings. Phase 1 is estimated at about 45 hours of work (roughly three weeks of evenings); see the cut order in [review.md](review.md) if time runs short.
- Clean-room: no artifacts from any employer. See ADR 0010 for the demo dataset.
- Local-first: everything runs on a Windows 11 PC with Docker Desktop and LM Studio; no cloud dependency for Phase 1.
- Paid API usage (DeepSeek) is reserved for v1 comparison runs; every eval run prints its estimated cost before starting and refuses to run above a configured cap.
- Documents and code are in English; no em dashes in any text.
- License: PolyForm Noncommercial 1.0.0 (ADR 0009).

## 8. Glossary

| Term | Meaning |
| --- | --- |
| Plan | The set of agents the supervisor picks for a request, for example `["data"]` or `["data", "docs"]`; its label joins the names with `+` |
| Agent | A registered component that declares an `AgentSpec` and returns an `AgentResult` |
| Agent spec | An agent's metadata: name, description for the supervisor prompt, cacheable, exclusive, needs |
| Port | An interface defined in `core` that the rest of the system depends on |
| Adapter | A concrete implementation of a port (for example the LangGraph orchestrator) |
| Tool source | Anything that can list and call tools; the first implementation is an MCP client |
| Platform tool | A read-only tool served by the platform itself, such as `result_page@1` |
| Run | One request through the platform, with its trace, answer and cost |
| Run token | A short-lived signed token minted by the API for one run; the tool server takes the tenant from it |
| Tool result view | The bounded part of a tool result that the model sees |
| Tenant | A logical customer of the platform with its own config, documents and permissions |
| Quality gate | Checks after an agent answers that can trigger one corrective action |
| Corrective action | Either a retry of the same agent with feedback, or a re-plan |
| Index version | A per-tenant counter bumped on every ingestion; used for cache invalidation |
