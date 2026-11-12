# Switchboard

**A source-available multi-agent platform where every routing decision is traced, every tool call is read-only and audited, and every number on the README was measured.**

Switchboard takes a user question, lets a supervisor decide whether to answer directly, search documents, query a business database through MCP tools, or do several of these in parallel, and then shows you exactly what happened and what it cost. Adding a new agent, tool, model provider, retriever, cache strategy or evaluation suite means implementing one interface and registering it, not changing core code.

[![CI](https://github.com/z27833009/switchboard/actions/workflows/ci.yml/badge.svg)](https://github.com/z27833009/switchboard/actions/workflows/ci.yml)

The badge reports the CI gate. That gate replays recorded model output with the mock provider, so it measures the pipeline (parsing, plan validation, tool normalization, scoring), not the model. See [ADR 0008](docs/adr/0008-evaluation-strategy.md).

> Status: **Phase 1 complete** (T1.01 to T1.18). The evaluation harness has measured reports on LM Studio. Every metric in the docs is a target until a report backs it, and the status column in the requirements links the measured ones.

## Measured results

Dataset v1, qwen3-8b on LM Studio, full default split with no limit, run on 2026-11-12. The routing suite scored 39 of 39 test cases and the toolcall suite 40 of 40 cases.

| Suite | Result | Latency |
| --- | --- | --- |
| Routing (plan graph) | Plan accuracy 0.5385, below the 0.75 local target (Jaccard 0.6667, ambiguous cases 0.6000) | p50 2353 ms, p95 2991 ms |
| Tool call | Tool name and argument accuracy both 0.9500, above the 0.60 local target; 0.95 calls per case, no redundant calls | p50 3407.5 ms, p95 3747.8 ms |

Reports: [routing](evals/reports/routing-lmstudio.md), [tool call](evals/reports/toolcall-lmstudio.md). Latency is one model call per case, not an end-to-end run. Cost is not in USD: local models have no API cost, and no price table exists until v1. The mock reports under [evals/reports/](evals/reports/) replay recorded model output, so the CI gate measures the pipeline, not the model.

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

The first request after loading the model can be slow while LM Studio loads it; the first measured routing run had p95 68.27 s from the one-time load. Later model calls in the committed reports took about 2.4 to 3.4 s.

### Troubleshooting

- `/health` reports `lmstudio` as error: check that the server is on port 18080, that `--bind 0.0.0.0` is set, and that `curl http://localhost:18080/v1/models` works on the host.
- `/health` reports `mcp_olist` as error: `docker compose logs mcp-olist`. The server refuses to start if `tool_audit` or `spans` is missing, which means the migrations in step 4 have not been applied to this volume.
- A port is already in use: stop the other stack, or change the host side of the mapping in `docker-compose.yml` and the matching URL in `.env`.
- `docker compose down` keeps the database volume. `docker compose down -v` deletes it, including the loaded data and runs.

## Five minute demo

[docs/demo.md](docs/demo.md) walks through the routing decision, a document citation, a tool citation with its audit row, a merged plan and the measured eval reports, with exact commands, questions and UI paths. It also contains the walkthrough "add a tool in three steps".

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
| [docs/demo.md](docs/demo.md) | Five minute demo script and the "add a tool in three steps" walkthrough |
| [docs/spec/requirements.md](docs/spec/requirements.md) | Goals, non-goals, user stories with acceptance criteria, quality metrics with targets |
| [docs/spec/design.md](docs/spec/design.md) | Architecture, ports and adapters, diagrams, data model, error handling, evaluation design |
| [docs/spec/tasks.md](docs/spec/tasks.md) | Phased roadmap: Phase 1 (smallest impressive demo), v1, v2 |
| [docs/spec/review.md](docs/spec/review.md) | Critical self-review: risks, over-engineering, what to cut |
| [docs/adr/](docs/adr/) | Architecture decision records |

## Stack

Python 3.12 with uv, FastAPI, LangGraph (as one orchestrator adapter), MCP (FastMCP), PostgreSQL 16 with pgvector, OpenTelemetry, TypeScript and React for the web UI, Docker Compose. Cloud deployment (Azure, Terraform) is an optional later phase.

## Continuous integration

CI runs lint, type checks, unit and contract tests, the integration tests against a PostgreSQL 16 service and a locally started Olist MCP server, and the routing and toolcall eval suites on the `mock` provider. The mock provider replays recorded model output, so the eval gate measures the pipeline (parsing, plan validation, tool normalization, scoring), not the model. A prompt change alters the fixture keys, which forces a re-record against a real model and produces a new report. The gate compares against the committed files in [evals/baselines/](evals/baselines/) and fails on a drop larger than 0.05; baselines are updated by committing a new file. See [ADR 0008](docs/adr/0008-evaluation-strategy.md).

## License

Switchboard is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, modify and share it for noncommercial purposes with attribution to the author, Jiajun (GitHub: z27833009). Commercial use requires a separate license from the author.

The full Olist dataset is licensed CC BY-NC-SA 4.0 by its authors and is not redistributed here. A 1000-order subset for tests lives in [data/olist/sample/](data/olist/sample/) under the same license, with attribution.
