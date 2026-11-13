# Switchboard

**A source-available multi-agent platform where every routing decision is traced, every tool call is read-only and audited, and every measured number links to a committed report.**

Switchboard takes a user question, lets a supervisor decide whether to answer directly, search documents, query a business database through MCP tools, or do several of these in parallel, and then shows you exactly what happened and what it cost. Adding a new agent, tool, model provider, retriever, cache strategy or evaluation suite means implementing one interface and registering it, not changing core code.

[![CI](https://github.com/z27833009/switchboard/actions/workflows/ci.yml/badge.svg)](https://github.com/z27833009/switchboard/actions/workflows/ci.yml)

The badge reports the CI gate. That gate replays recorded model output with the mock provider, so it measures the pipeline (parsing, plan validation, tool normalization, scoring), not the model. See [ADR 0008](docs/adr/0008-evaluation-strategy.md).

> Status: **Phase 1 complete, and v1 is built through T2.18.** T2.01 to T2.15 and T2.17 to T2.18 are merged. T2.16 (optional `langgraph-visualizer` integration) was cancelled by the author on 2026-11-12. The semantic cache is implemented and measured; its false-hit rate is above the 2 percent target, so the demo tenants keep it disabled and the report says why. T2.12's comparison tool and all six LM Studio rows are committed; the six DeepSeek rows are `not run` because no `DEEPSEEK_API_KEY` is available. Every metric in the docs is a target until a report backs it.

## Measured results

All model rows were measured on LM Studio 1.2.0 with qwen3-8b at context 8192 and one parallel slot, on the dates in each report. Routing scores the 39 test cases of dataset v1, tool call all 40 v1 cases, retrieval the 40 test cases of v3, and the cache the 50 pairs of v5; the gate and injection suites are deterministic and make no model call, and the cache suite makes no chat call (it embeds with bge-m3).

| Suite | Result | Latency | Report |
| --- | --- | --- | --- |
| Routing (plan graph) | Plan accuracy 0.9231, Jaccard 0.9359, unambiguous 0.9706, ambiguous 0.6000, 39 of 39 test cases; above the 0.75 local target | p50 4466.0 ms, p95 5551.7 ms | [routing-lmstudio.md](evals/reports/routing-lmstudio.md) |
| Tool call | Tool name and argument accuracy both 0.9500, 0.95 calls per case, no redundant calls; above the 0.60 local target | p50 3407.5 ms, p95 3747.8 ms | [toolcall-lmstudio.md](evals/reports/toolcall-lmstudio.md) |
| Retrieval (rag suite) | Conditional hybrid recall@5 1.0000, MRR 0.8521; query as written 1.0000 and 0.8250; always rewrite 0.9750 and 0.8521; 40 of 40 v3 cases | p50 606.0 ms, p95 1218.8 ms per retrieval case | [rag-lmstudio.md](evals/reports/rag-lmstudio.md) |
| Quality gate (deterministic, 30 cases v2) | `numbers_grounded` precision 0.9000, recall 1.0000; citations, evidence and merge checks 1.0000/1.0000; case accuracy 0.9667 | no model call | [gate-mock.md](evals/reports/gate-mock.md) |
| Injection (deterministic, 40 cases v4) | Detection rate 1.0000, false positive rate 0.1000, precision 0.9091 | no model call | [injection-mock.md](evals/reports/injection-mock.md) |
| Semantic cache (bge-m3, 50 pairs v5) | At the chosen 0.94 threshold: hit rate 0.0400, false-hit rate 0.0400; at 0.99 both 0.0000. The 0.02 false-hit target is not met, so the demo tenants keep the cache off | embedding only, one batch | [cache-lmstudio.md](evals/reports/cache-lmstudio.md) |

Latency is one model call per case for routing and tool call, not an end-to-end run; retrieval latency is the per-case wall time of one strategy. The LM Studio runs have no API cost: `config/prices.yaml` records a zero row for the local provider, so a new run or report computes USD 0.0 and labels it "no API cost". The routing and tool call reports predate the price table and keep a null cost rather than a backfilled number; the newer agent-loop reports record 0.0. The mock reports under [evals/reports/](evals/reports/) replay recorded model output, so the CI gate measures the pipeline, not the model.

## Provider and orchestrator comparison

`switchboard eval compare` merges the per-suite JSON reports into one table with provider and orchestrator as dimensions. The committed table is [evals/reports/compare.md](evals/reports/compare.md) (JSON: [compare.json](evals/reports/compare.json)); it links every per-suite report and is written with:

```bash
uv run switchboard eval compare --not-run-reason "deepseek=DEEPSEEK_API_KEY missing"
```

The six LM Studio rows were measured on 2026-11-12 (langgraph) and 2026-11-13 (agent loop) with qwen3-8b, on dataset v1 (routing test split, toolcall all cases) and v3 (rag test split). The comparison covers 6 of the 12 provider and orchestrator combinations: every LM Studio row is measured, and every DeepSeek row is `not run: DEEPSEEK_API_KEY missing`. These rows have no API cost.

| Suite | Orchestrator | Score | p50 / p95 | Tokens per 100 queries |
| --- | --- | --- | --- | --- |
| Routing | langgraph | plan accuracy 0.9231, Jaccard 0.9359 | 4466.0 / 5551.7 ms | 54156.4 |
| Routing | agent_loop | plan accuracy 0.6410, Jaccard 0.7564 | 1132.0 / 1870.2 ms | 131517.9 |
| Tool call | langgraph | both accuracy 0.9500 | 3407.5 / 3747.8 ms | 88115.0 |
| Tool call | agent_loop | both accuracy 0.9250 | 1296.5 / 1501.4 ms | 121950.0 |
| Rag | langgraph | recall@5 1.0000, MRR 0.8521 | 606.0 / 1218.8 ms | 19345.0 |
| Rag | agent_loop | recall@5 1.0000, MRR 0.8521 | 595.0 / 1157.3 ms | 19345.0 |

Per-suite reports: [routing langgraph](evals/reports/routing-lmstudio.md), [routing agent loop](evals/reports/routing-agent_loop-lmstudio.md), [tool call langgraph](evals/reports/toolcall-lmstudio.md), [tool call agent loop](evals/reports/toolcall-agent_loop-lmstudio.md), [rag langgraph](evals/reports/rag-lmstudio.md), [rag agent loop](evals/reports/rag-agent_loop-lmstudio.md). The agent-loop routing and tool-call rows score the first loop decision with the loop's full toolset; the suite does not execute tools, so a live loop can differ. Rag measures retrieval, which both adapters share, so its two rows run the same strategy code and differ only by run-to-run timing. The three langgraph reports predate the price table and keep a null cost; the comparison applies the table's explicit zero row and labels them "no API cost" without changing those files.

The six DeepSeek rows (routing, toolcall and rag for both orchestrators) are **pending**: this environment has no `DEEPSEEK_API_KEY`, so the comparison marks them `not run: DEEPSEEK_API_KEY missing`. DeepSeek is configured in `config/providers.yaml` with verified prices in `config/prices.yaml`, but it has not been live-tested. With a key in `.env`, the exact commands are:

```bash
uv run --env-file .env switchboard eval run --suite routing --provider deepseek --confirm-cost
uv run --env-file .env switchboard eval run --suite routing --provider deepseek --orchestrator agent_loop --confirm-cost --concurrency 1
uv run --env-file .env switchboard eval run --suite toolcall --provider deepseek --confirm-cost --concurrency 1
uv run --env-file .env switchboard eval run --suite toolcall --provider deepseek --orchestrator agent_loop --confirm-cost --concurrency 1
uv run --env-file .env switchboard eval run --suite rag --provider deepseek --dataset-version v3 --confirm-cost --concurrency 1
uv run --env-file .env switchboard eval run --suite rag --provider deepseek --orchestrator agent_loop --dataset-version v3 --confirm-cost --concurrency 1
uv run --env-file .env switchboard eval compare
```

## Quick start

This path is tested from a fresh clone on Windows 11 with Docker Desktop and LM Studio on the host. It starts four containers, loads the 1000-order sample and ingests five synthetic policy documents. The five minute tour is in [docs/demo.md](docs/demo.md).

### Prerequisites

| Tool | What you need |
| --- | --- |
| Docker Desktop | Compose v2. Give Docker at least 8 GB of disk for images and the database volume. |
| LM Studio | Version 1.2 or later (the Bionic line), with `qwen3-8b` and `bge-m3` downloaded. The stack calls an OpenAI-compatible server on the host. |
| uv | Any recent `uv`; the image pins 0.11.21. `uv` also supplies Python 3.12 for the host commands. |
| Node.js | LTS, only if you build the web UI yourself. `docker compose` builds it inside an image, so the quick start does not need Node. |

### 1. Clone and install

```bash
git clone https://github.com/z27833009/switchboard
cd switchboard
uv sync --locked
```

`uv sync --locked` creates `.venv` from `uv.lock`. The `switchboard` commands below run through `uv run`, which uses that environment.

### 2. Configure the secret

```bash
cp .env.example .env
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the 64-character output into `SWITCHBOARD_RUN_TOKEN_SECRET=` in `.env`. The API and the MCP server both refuse to start without at least 32 bytes. Every other value in `.env.example` is a development value; none of them is a secret.

On PowerShell, `Copy-Item .env.example .env` does the copy. Open `.env` in an editor and paste the secret.

### 3. Start LM Studio on port 18080

In LM Studio, load the chat model with an 8192 token context and one parallel slot, load the embedding model, and start the local server on port 18080 bound to all interfaces:

```bash
lms load qwen3-8b -c 8192 --parallel 1 --identifier qwen3-8b -y
lms load bge-m3 --identifier bge-m3 -y
lms server start --port 18080 --bind 0.0.0.0
curl http://localhost:18080/v1/models
```

The last command must list `qwen3-8b` and `bge-m3`.

LM Studio defaults to port 1234. On the development machine TCP 1234 falls inside a Windows excluded port range (1210 to 1309), so the compose file, `.env.example` and the committed reports all use 18080. Run `netsh interface ipv4 show excludedportrange protocol=tcp` to see the ranges on your machine. Use 18080 for the documented path; changing it means editing `docker-compose.yml`, because the `api` service reaches the host as `host.docker.internal:18080`.

Keep `--parallel 1`. The committed reports were measured with one slot at context 8192. On the development machine, the same supervisor call took 28 s with four parallel slots loaded instead of about 2.3 s with one.

### 4. Build and start the stack

```bash
docker compose up -d --build
docker compose ps
```

The first build installs Python and Node dependencies inside the images, so it can take a few minutes on a cold cache. Wait until all four services (`postgres`, `mcp-olist`, `api`, `web`) show `healthy`.

```bash
curl http://localhost:8000/health
```

Expect `"status":"ok"` and `ok` for `postgres`, `mcp_olist` and `lmstudio`. The endpoint returns 503 when any dependency is down, and names the failing one.

If you already have a database volume from an earlier Phase 1 checkout, apply the later migrations once. Fresh volumes apply them automatically in filename order, and the files are idempotent:

```bash
docker compose exec -T postgres psql -U switchboard -d switchboard \
  -f /docker-entrypoint-initdb.d/02-platform.sql
docker compose exec -T postgres psql -U switchboard -d switchboard \
  -f /docker-entrypoint-initdb.d/03-documents.sql
docker compose exec -T postgres psql -U switchboard -d switchboard \
  -f /docker-entrypoint-initdb.d/04-runs-spans.sql
docker compose exec -T postgres psql -U switchboard -d switchboard \
  -f /docker-entrypoint-initdb.d/05-feedback.sql
```

### 5. Load data

The fast path uses the committed 1000-order sample:

```bash
uv run --env-file .env switchboard data load-olist data/olist/sample \
  --expected data/olist/sample/expected_counts.json
```

For the full 99,441-order download, follow [data/olist/README.md](data/olist/README.md) and load `data/olist/raw` with the same command. The loader truncates and reloads every table, so load either the sample or the full data. The full data makes the demo questions return realistic counts.

### 6. Ingest the policy documents

```bash
uv run --env-file .env switchboard ingest data/documents --tenant default
```

On a fresh database this reports `documents added 5, updated 0, unchanged 0` and `chunks written 29`. The command is idempotent: a second run reports zero changes.

The cross-tenant demo uses a second tenant, `seller_a`, restricted to one seller. Give it the same documents with `uv run --env-file .env switchboard ingest data/documents --tenant seller_a`; retrieval, the run list and traces are then tenant isolated in the UI and the API.

### 7. Open it

| What | URL |
| --- | --- |
| Web UI | http://localhost:8080/#/chat |
| Run list | http://localhost:8080/#/runs |
| API docs | http://localhost:8000/docs |
| API health | http://localhost:8000/health |
| Health through the web proxy | http://localhost:8080/api/health |
| Tool server health | http://localhost:8001/health |

Open the chat page, keep tenant `default` and ask:

> What is the return window for electronics?

The answer comes from `data/documents/returns-policy.md` and carries a `[d1]` citation. Click the citation chip to open the run trace.

The same request from a terminal, on bash, macOS or Linux:

```bash
curl -s http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the return window for electronics?","tenant_id":"default"}'
```

On PowerShell:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/chat -Method Post `
  -ContentType 'application/json' `
  -Body '{"query":"What is the return window for electronics?","tenant_id":"default"}'
```

The first request after loading the model can be slow while LM Studio loads it; the first measured routing run had p95 68.27 s from the one-time load. Later model calls in the committed reports took about 3.4 to 4.5 s (tool call p50 3407.5 ms, routing p50 4466.0 ms).

Rate an answer with the thumbs up or down under the answer, or on the run page, with an optional note. Ratings are stored per tenant and append-only. `uv run --env-file .env switchboard eval harvest --since 2026-11-01` writes the negatively rated runs of that date onward to `evals/candidates/` as unlabeled cases; [evals/candidates/README.md](evals/candidates/README.md) states the format. The command never edits a frozen dataset.

### Traces in Jaeger (optional)

The default span exporter is the PostgreSQL `spans` table, which the trace page reads. The same spans can go to Jaeger through the optional `tracing` compose profile. It is one switch and the default stack never depends on it:

```powershell
$env:SWITCHBOARD_TRACING_EXPORTER="otlp"; docker compose --profile tracing up -d
```

On bash or macOS, the same line is `SWITCHBOARD_TRACING_EXPORTER=otlp docker compose --profile tracing up -d`. The profile writes `tracing.exporter: otlp` through the environment for both the API and the MCP server, and the containers send OTLP/HTTP to `http://jaeger:4318`. Run a chat request as above, open http://localhost:16686, select the `switchboard-api` service and search by the run id or the `switchboard.run_id` tag. The tool span also appears from the `mcp-olist` service, in the same trace.

To return to the default, run `Remove-Item Env:SWITCHBOARD_TRACING_EXPORTER` in that PowerShell session (or unset it in bash), then `docker compose up -d` and `docker compose --profile tracing down`. While `otlp` is selected the run trace page and `GET /runs/{id}/trace` are empty, because the spans go to Jaeger instead of PostgreSQL; the run row still records whether the flush succeeded. Jaeger listens on 16686 (UI), 4318 (OTLP/HTTP) and 4317 (OTLP/gRPC). Host-side OTLP commands use `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` from `.env`.

### Troubleshooting

- `/health` reports `lmstudio` as error: check that the server is on port 18080, that `--bind 0.0.0.0` is set, and that `curl http://localhost:18080/v1/models` works on the host.
- `/health` reports `mcp_olist` as error: `docker compose logs mcp-olist`. The server refuses to start if `tool_audit` or `spans` is missing, which means the migrations in step 4 have not been applied to this volume.
- A port is already in use: stop the other stack, or change the host side of the mapping in `docker-compose.yml` and the matching URL in `.env`.
- `docker compose down` keeps the database volume. `docker compose down -v` deletes it, including the loaded data and runs.

## Five minute demo

[docs/demo.md](docs/demo.md) walks through the routing decision, a document citation, a tool citation with its audit row, a merged plan and the measured eval reports, with exact commands, questions and UI paths. It also contains the walkthrough "add a tool in three steps".

## Why this exists

Most "agent platform" demos hide three things: how the router decided, how often it was wrong, and what the run cost. Switchboard is built around making those three visible:

- A supervisor with a structured routing decision (`direct`, `docs`, `data`, or both) and a quality gate that can retry or re-plan a weak answer once.
- An MCP tool server over a public e-commerce dataset (Olist), exposing parameterized, read-only tools with a per-tenant permission model.
- A model layer that treats every OpenAI-compatible endpoint (LM Studio locally, DeepSeek in the cloud) the same way, so providers can be compared on the same test set.
- OpenTelemetry instrumentation from day one, stored in PostgreSQL and rendered in a trace view.
- A versioned evaluation harness with a regression gate in CI.

## Documents

| Document | Purpose |
| --- | --- |
| [docs/demo.md](docs/demo.md) | Five minute demo script and the "add a tool in three steps" walkthrough |
| [docs/interview-notes.md](docs/interview-notes.md) | Five minute interview walkthrough of the decisions, with measured numbers and known limitations |
| [docs/spec/requirements.md](docs/spec/requirements.md) | Goals, non-goals, user stories with acceptance criteria, quality metrics with targets |
| [docs/spec/design.md](docs/spec/design.md) | Architecture, ports and adapters, diagrams, data model, error handling, evaluation design |
| [docs/spec/tasks.md](docs/spec/tasks.md) | Phased roadmap: Phase 1 (smallest impressive demo), v1, v2 |
| [docs/spec/review.md](docs/spec/review.md) | Critical self-review: risks, over-engineering, what to cut |
| [docs/adr/](docs/adr/) | Architecture decision records |

## Stack

Python 3.12 with uv, FastAPI, LangGraph (as one orchestrator adapter), MCP (FastMCP), PostgreSQL 16 with pgvector, OpenTelemetry, TypeScript and React for the web UI, Docker Compose. Cloud deployment (Azure, Terraform) is an optional later phase.

## Continuous integration

CI runs lint, type checks, unit and contract tests, the integration tests against a PostgreSQL 16 service and a locally started Olist MCP server, and the routing, toolcall and gate eval suites on the `mock` provider. The routing and toolcall suites replay recorded model output, so the eval gate measures the pipeline (parsing, plan validation, tool normalization, scoring), not the model. The gate suite makes no model call at all: it runs the deterministic checks over the 30 labeled cases in dataset v2 and reports per-check precision and recall. The rag and injection suites are not part of the CI gate: rag needs the database and the embedding model, and injection is measured by its own committed report. A prompt change alters the fixture keys, which forces a re-record against a real model and produces a new report. The regression gate compares against the committed files in [evals/baselines/](evals/baselines/) and fails on a drop larger than 0.05; baselines are updated by committing a new file. See [ADR 0008](docs/adr/0008-evaluation-strategy.md).

## License

Switchboard is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, modify and share it for noncommercial purposes with attribution to the author, Jiajun (GitHub: z27833009). Commercial use requires a separate license from the author.

The full Olist dataset is licensed CC BY-NC-SA 4.0 by its authors and is not redistributed here. A 1000-order subset for tests lives in [data/olist/sample/](data/olist/sample/) under the same license, with attribution.
