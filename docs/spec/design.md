# Switchboard Design

Version 0.2, 2026-11-12. Status: draft for review. Companion to [requirements.md](requirements.md) and the ADRs in [docs/adr](../adr/). Changes since 0.1 are listed in [review.md](review.md) section 0.

## 1. Overview

Switchboard is a modular monolith built in the ports and adapters style. A small framework-free core defines the interfaces (ports) and domain models. Everything that talks to the outside world (LangGraph, model APIs, MCP, PostgreSQL, OpenTelemetry) is an adapter behind one of those ports and is selected by configuration. Three things run as separate processes because the boundary is real: the MCP tool server (protocol and permission boundary), the web UI (different technology), and PostgreSQL.

The platform answers a question by choosing a **plan**: a set of one or more registered agents (for example `data`, `docs`, or `data` plus `docs` in parallel). Agents, tools, providers, retrievers and eval suites are registered, not hard-coded, so adding one does not change the core or the orchestrator (ADR 0011).

```mermaid
flowchart LR
  U[User or demo script] --> WEB[Web UI<br/>TypeScript, React]
  WEB -- HTTP and SSE --> API[API<br/>FastAPI]
  EV[Eval CLI] --> CORE
  API --> CORE[core<br/>ports, domain models, registry, context policy]
  CORE --> ORCH[Orchestrator adapter<br/>LangGraph plan graph<br/>v1: agent loop]
  CORE --> PROV[ModelProvider adapter<br/>OpenAI-compatible]
  CORE --> RET[Retriever adapter<br/>pgvector + full text]
  CORE --> CACHE[Cache adapter<br/>pgvector semantic cache]
  CORE --> TR[Tracing<br/>OTel SDK + Postgres exporter<br/>+ step event stream]
  ORCH --> TS[ToolSource adapter<br/>MCP client]
  TS -- MCP over HTTP<br/>signed run token --> MCP[MCP tool server<br/>FastMCP, separate process]
  PROV -- HTTP --> LM[LM Studio on host]
  PROV -- HTTPS --> DS[DeepSeek API, v1]
  MCP --> PG[(PostgreSQL 16<br/>pgvector)]
  RET --> PG
  CACHE --> PG
  TR --> PG
```

## 2. Principles

1. **Core depends on nothing.** `src/switchboard/core` imports only the standard library and pydantic. Enforced by an import-linter contract in CI.
2. **One port, one job.** Each port has a single responsibility and a contract test suite that every adapter must pass.
3. **Configuration and registration select behavior.** YAML names the adapter; the registry resolves it through Python entry points. The set of agents is registry data, never an enum (ADR 0011).
4. **Everything is traced.** Every port call that crosses a process or network boundary creates a span. No span, no feature. Code uses the OTel API from the first boundary call (T1.05); until the exporter exists (T1.12) the global tracer is a no-op, so spans are written from the start and become visible later.
5. **Numbers come from the harness.** No metric is written anywhere without a report file to back it.
6. **Read-only by default.** Tools cannot write. The database role for tools has SELECT only.
7. **Tenant id is not optional.** Every request, record, span and cache entry carries a tenant id even while there is only one tenant.
8. **Evidence over assertion.** Every number in an answer points to the tool call that produced it and every policy statement to a document chunk. Checks that can be deterministic are deterministic (ADR 0013).
9. **Bounded context.** The model sees a bounded view of tool output; full results live in storage and the trace. Compaction prefers lossless steps and never summarizes with a model in Phase 1 or v1 (ADR 0012).

## 3. Components and responsibilities

| Component | Location | Responsibility | Depends on |
| --- | --- | --- | --- |
| core | `src/switchboard/core` | Ports, domain models, errors, registry, configuration schema, context policy, guardrail patterns | nothing |
| agents | `src/switchboard/agents` | `DirectAgent`, `DocsAgent`, `DataAgent`, `ClarifyAgent` (v1), `MergeAgent` (not routable); each routable agent implements `Agent` and declares an `AgentSpec` | core ports |
| orchestrator adapters | `src/switchboard/adapters/orchestrator` | `langgraph_impl`: plan graph (cache lookup, supervisor, fan-out, merge, gate, corrective action). `agent_loop` (v1): single model with all tools in a bounded loop, for comparison | core, agents, langgraph |
| provider adapters | `src/switchboard/adapters/providers` | `OpenAICompatibleProvider` (LM Studio, DeepSeek), `MockProvider` (recorded responses) | core, openai SDK |
| embedder adapters | `src/switchboard/adapters/embedders` | `OpenAICompatibleEmbedder`, `MockEmbedder` | core |
| tool source adapters | `src/switchboard/adapters/tools` | `McpToolSource` (client, sends run token), `InProcessToolSource` (tests and platform tools such as `result_page`) | core, mcp SDK |
| retriever adapters | `src/switchboard/adapters/retrievers` | `PgVectorRetriever` (vector, v1 adds full text and RRF), query rewriting (v1), ingestion pipeline | core, asyncpg |
| cache adapters | `src/switchboard/adapters/cache` | `NoCache`, `PgSemanticCache` (v1) | core, asyncpg |
| tracing | `src/switchboard/tracing` | OTel setup, `PostgresSpanExporter`, `StepEventProcessor` (SSE source), span helpers, cost accounting | core, opentelemetry |
| api | `src/switchboard/api` | FastAPI app: chat, chat stream, runs, trace, feedback (v1), audit (v1), health; mints run tokens | core, tracing, PyJWT |
| eval | `src/switchboard/eval` | Suites, scorers, runner, report writer, baseline comparison, feedback harvest (v1) | core |
| mcp server | `mcp_servers/olist` | FastMCP server with read-only tools over the Olist schema, tool manifest, run token verification, permission check, audit write | mcp SDK, asyncpg, PyJWT; does not import switchboard core |
| web | `web/` | Chat page with live steps and citation chips, trace page | API only |
| data | `data/` | Olist loader, synthetic policy documents, SQL schema | PostgreSQL |

`PyJWT` is the only dependency added in 0.2. It signs and verifies run tokens (7.3); writing HMAC token handling by hand would be more code and less recognizable.

## 4. Ports and domain models

All ports are `typing.Protocol` classes with pydantic models for inputs and outputs. Signatures below are the intended shape; names are stable. Types mentioned but not spelled out here (`Message`, `Turn`, `Budget`, `EvidenceRef`, `GateVerdict`, `ChatRequest`, `RunResult`, `ToolResult`, `ToolCallRecord`, `CacheKey`, `CachedAnswer`, `ContextBudget`, `FitResult`, settings models) are defined in T1.04. After T1.04 they are a contract: a later task that changes a field says so in its commit message and updates this section in the same commit.

```python
class ModelProvider(Protocol):
    name: str
    def capabilities(self) -> ProviderCapabilities: ...   # tools, json_schema, streaming, reasoning, context_window
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

class Embedder(Protocol):
    name: str
    dimension: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

class ToolSource(Protocol):
    name: str
    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]: ...
    async def call(self, name: str, args: dict, ctx: RunContext) -> ToolResult: ...

class Retriever(Protocol):
    name: str
    # ``round`` is 1 or 2; the retriever records it on the ``retrieve`` span.
    # ``embedding`` is the cache's vector for the same query, reused on a miss.
    async def retrieve(self, query: str, ctx: RunContext, k: int = 5, *,
                       round: int = 1,
                       embedding: list[float] | None = None) -> list[RetrievedChunk]: ...

class Cache(Protocol):
    name: str
    # Build the lookup key: normalize the query, embed it and read the tenant's
    # current document index version (v1). A miss reuses the returned embedding.
    async def key_for(self, ctx: RunContext, query: str) -> CacheKey: ...
    async def lookup(self, key: CacheKey, *, threshold: float = 0.92) -> CacheHit | None: ...
    async def store(self, key: CacheKey, answer: CachedAnswer, *,
                    ttl_days: int = 7) -> None: ...
    async def invalidate(self, tenant_id: str) -> int: ...

class Agent(Protocol):
    spec: AgentSpec
    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult: ...

class Orchestrator(Protocol):
    name: str
    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult: ...

class ContextPolicy(Protocol):
    def fit(self, messages: list[Message], budget: ContextBudget) -> FitResult: ...

class EvalSuite(Protocol):
    name: str
    def load(self, version: str) -> list[EvalCase]: ...
    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome: ...
    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score: ...
```

Key domain models:

```python
class AgentSpec(BaseModel):
    name: str                  # "direct", "docs", "data", "clarify"
    description: str           # one line, rendered into the supervisor prompt
    cacheable: bool            # may answers from this agent be cached
    exclusive: bool = False    # must be the only agent in a plan (direct, clarify)
    needs: list[str] = []      # "tools", "retriever"; used to hide agents a tenant cannot run

class RoutingDecision(BaseModel):
    agents: list[str]          # non-empty, registered, allowed for the tenant
    reason: str

    @property
    def label(self) -> str:    # "data", "data+docs"; used in UI, spans, eval
        return "+".join(sorted(self.agents))

class CompletionRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSpec] = []
    response_schema: dict | None = None   # JSON schema for structured output
    temperature: float = 0.0
    max_tokens: int | None = None
    stream: bool = False                  # v1, answer tokens only
    delta_callback: Callable[[str], None] | None = None  # v1, answer fragments; excluded from dumps
    metadata: dict = {}                   # run_id, tenant_id, purpose

class Usage(BaseModel):
    input_tokens: int
    cached_input_tokens: int = 0          # served from the provider's prefix cache
    output_tokens: int

class CompletionResponse(BaseModel):
    text: str | None
    reasoning: str | None = None      # separated from the answer text
    tool_calls: list[ToolCall]
    parsed: dict | None        # when response_schema was given
    structured_output_strategy: Literal["native_json_schema", "tool_call", "prompted_json"] | None
    usage: Usage
    model: str
    provider: str
    latency_ms: int
    cost_usd: float | None = None   # per call from the price row (12.4)

class ToolSpec(BaseModel):
    name: str
    version: str               # semver, major in the wire name: orders_late@1
    description: str
    input_schema: dict
    risk: Literal["read", "write"] = "read"
    tenant_scoped: bool = False
    sensitive_args: list[str] = []

class ToolResultView(BaseModel):   # what the model sees (8.6)
    result_id: str
    tool: str
    total_rows: int
    summary: dict | None
    rows_preview: list[dict]   # at most limits.model_view_rows (default 20)
    truncated: bool

class Citation(BaseModel):
    kind: Literal["document", "tool"]
    ref: str                   # marker used in the answer text: "d1", "t2"
    source_id: str             # chunk id or tool call id
    span_id: str | None        # links the UI chip to the trace
    audit_id: str | None       # tool citations only
    snippet: str

class AgentTask(BaseModel):
    query: str
    history: list[Turn] = []   # v1, last N turns of the session
    feedback: str | None = None  # set on a corrective retry (6.8)
    budget: Budget
    query_embedding: list[float] | None = None  # v1, cache vector for reuse (10.1)

class AgentResult(BaseModel):
    answer: str
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    evidence: list[EvidenceRef]  # ids of chunks and tool results the agent saw, for the gate
    usage: Usage
    cost_usd: float | None = None   # sum of this agent's calls (12.4)
    partial: bool = False      # set when evidence was missing or a branch failed
    needs_clarification: bool = False  # v1: answer is a question back to the user

class EvidenceRef(BaseModel):
    kind: Literal["document", "tool"]
    ref: str                   # marker the agent used, "d1" or "t1"
    source_id: str             # chunk id or tool result id
    text: str = ""             # content the agent saw, for the deterministic gate (6.8)
    empty: bool = False        # an empty tool result or blank chunk

class GateVerdict(BaseModel):
    passed: bool
    failed_checks: list[str] = []
    action: Literal["pass", "retry", "replan", "none"] = "pass"
    reason: str = ""
    feedback: str | None = None

class RunContext(BaseModel):
    run_id: str
    tenant: TenantContext
    run_token: str             # signed, see 7.3
    trace_parent: str          # W3C traceparent for cross-process spans

class TenantContext(BaseModel):
    tenant_id: str
    allowed_agents: set[str]
    allowed_tools: set[str]
    ask_tools: set[str]        # v2, human approval
    seller_ids: list[str] | None   # resolved server-side; never sent by the model
    provider: str
    retrieval: RetrievalSettings
    cache: CacheSettings
```

There is no `Route` enum. The routing decision carries no self-reported confidence: a model's self-assessed confidence is not calibrated and nothing in the design consumed it, so it was removed rather than logged as if it meant something.

`RunResult` carries the final `plan`, the `previous_plan` set on a re-plan, the final `gate` verdict, `attempts` and `errors`, so a caller can see what the gate rejected and which corrective action ran (6.8). It also carries `cost_usd`: the run's USD cost computed from the summed `usage` and the price row for its provider (12.4), or `None` when that provider has no row.

Dependencies are injected through `AgentDeps` and `RunContext` (provider, embedder, tool sources, retriever, cache, context policy, tracer, and in v1 the answer delta sink), never imported globally. `AgentDeps.answer_delta` is `None` unless the caller wants live answer tokens; an agent that has one attaches it to a streamed answer call and the provider calls it with each text fragment (8.1).

Sessions (v1). `ChatRequest.session_id` is optional. When it is set, the API loads the last `limits.history_turns` completed runs of that tenant and session from the `runs` table, oldest first, as user and assistant `Turn`s, and puts them in `ChatRequest.history`; a client-supplied `history` is ignored in that case, so server-side stored history is authoritative and a browser only has to keep a session id. Without a session id the client history is passed through unchanged. The orchestrator gives the same `AgentTask.history` to the supervisor prompt and to every agent, which is what lets a follow-up such as "And in April?" be resolved (6.3, 9.3). The tenant and session id are both in the SQL filter (11).

`AgentResult.needs_clarification` is the generic v1 marker for an answer that is a question back to the user. The orchestrator sets `RunStatus` `needs_clarification` when a single-agent plan produced such a result; it never checks an agent name, so a new clarification agent is one class plus one entry point (6.4).

Orchestrator override (v2 T3.07). `ChatRequest.orchestrator` is optional. When it is set, `POST /chat` and `POST /chat/stream` run that one request on the registered orchestrator of that name instead of the platform default in `platform.yaml`; `None` keeps the default path unchanged. A name that is not registered, or whose optional instance could not be built, is a 400 before any run row exists. The API builds the platform orchestrator once per tenant provider as before, and one optional instance per (tenant provider, other registered orchestrator name), sharing the provider, embedder, retriever, cache, tool sources, context policy and price row. An optional target that cannot be built (for example a provider whose API key is absent) is skipped with a warning rather than failing startup. The run row records the name of the orchestrator that actually ran, so a replay is distinguishable from the original. This override changes the orchestrator only: the request still runs on the tenant's configured provider, because provider selection is not part of this task.

## 5. Request flow

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant O as Orchestrator (LangGraph)
    participant K as Cache (v1)
    participant S as Supervisor node
    participant D as DataAgent
    participant R as DocsAgent
    participant T as MCP server
    participant M as ModelProvider
    participant G as Gate (v1)

    C->>A: POST /chat/stream {query, tenant_id}
    A->>A: insert run row (status running), mint run token
    A->>O: run(request, ctx)   [root span: run]
    A-->>C: SSE run.started
    O->>K: lookup(tenant, embedding, index_version)
    K-->>O: miss
    O->>S: plan(query)
    S->>M: complete(response_schema=RoutingDecision)
    M-->>S: {agents: [data, docs], reason}
    A-->>C: SSE plan.decided
    par data branch
        O->>D: run(task)
        D->>M: complete(tools=[...])
        M-->>D: tool_calls
        D->>T: call(orders_late@1, args) + Bearer run token
        T-->>D: rows, audit_id (read-only, audited)
        D->>D: build ToolResultView, fit context
        D->>M: complete(final answer with [t1] markers)
    and docs branch
        O->>R: run(task)
        R->>R: retrieve(query, k=5)
        R->>M: complete(chunks, answer with [d1] markers)
    end
    O->>O: merge(results)
    O->>G: check(answer, evidence)
    G-->>O: pass, retry with feedback, or re-plan
    O-->>A: RunResult
    A->>A: force_flush spans, update run row
    A-->>C: SSE run.finished {run_id, answer, plan, citations, usage}
```

`POST /chat` is the same flow without the event stream and returns the final payload.

## 6. Orchestration

### 6.1 Graph

The LangGraph adapter builds one generic graph. Nodes are thin: they call agents and ports, they contain no business logic, and no node is named after a specific agent.

```mermaid
flowchart TD
  start([start]) --> cache_lookup
  cache_lookup -->|hit| finish([finish])
  cache_lookup -->|miss or skipped| supervisor
  supervisor --> dispatch
  dispatch -. Send per agent in plan .-> run_agent
  run_agent --> join
  join -->|one result| gate
  join -->|several results| merge
  merge --> gate
  gate -->|pass| cache_store
  gate -->|retry with feedback, attempts = 0| run_agent
  gate -->|re-plan, attempts = 0| supervisor
  gate -->|fail, attempts = 1| finish_partial[finish with honest failure note]
  cache_store --> finish
```

`dispatch` uses LangGraph's `Send` API to start one `run_agent` branch per agent in the plan, so a plan of one, two or three agents uses the same nodes. Every node ships from the start, so the graph shape does not change between phases. From v1 (T2.01) `gate` runs the deterministic checks and may send work back to `run_agent` (retry) or `supervisor` (re-plan); from T2.18 `cache_lookup` runs before the supervisor and `cache_store` runs after a passing gate, both through the `Cache` port.

### 6.2 State

```python
class RunState(TypedDict):
    request: ChatRequest
    ctx: RunContext
    history: list[Turn]                                    # v1
    cache_hit: CacheHit | None
    plan: RoutingDecision | None
    previous_plan: RoutingDecision | None                  # set on re-plan
    results: Annotated[dict[str, AgentResult], merge_dicts]   # reducer: parallel branches write concurrently
    answer: AgentResult | None
    verdict: GateVerdict | None
    attempts: int
    feedback: str | None                                  # concrete feedback for a retry
    errors: Annotated[list[ErrorRecord], operator.add]
```

`results` and `errors` need reducers because parallel branches update them in the same step; without a reducer LangGraph raises on concurrent writes. `attempts` counts corrective actions, not agent calls, and stops at one (6.8).

### 6.3 Supervisor and plans

The supervisor is one structured-output call. Its prompt and its JSON schema are generated at run time from the registry:

```python
available = [a.spec for a in registry.agents()
             if a.spec.name in tenant.allowed_agents and needs_met(a.spec, deps)]
schema = plan_schema(enum=[s.name for s in available], max_items=limits.max_plan_size)
prompt_lines = [f"- {s.name}: {s.description}" for s in available]
```

The model can only choose agents that exist and that the tenant may use. After parsing, the plan is validated: non-empty, no duplicates, at most `limits.max_plan_size` agents (default 3), and an `exclusive` agent (`direct`, `clarify`) only on its own. A plan that fails validation gets one repair call; if that fails the supervisor falls back to the plan configured as `limits.fallback_plan` (default `["docs", "data"]`, the widest safe plan) and records a `PlanValidationError`.

On a re-plan (6.8) the prompt includes the previous plan and the gate's reason, and the new plan must differ from the previous one.

With history (v1) the dynamic user message also carries the recent turns, after the agent list and instructions and before the question, with one instruction to resolve the new question against them. The static system prompt and the prefix it forms never change with history, and without history the message is byte-identical to the Phase 1 prompt, so the routing eval fixtures and their measurement do not move (8.5, 20.2).

Adding an agent is one class with an `AgentSpec` and one entry point. The supervisor prompt, the schema, the graph and the cache rule pick it up without change.

### 6.4 Agent contract

An agent receives `AgentTask` and `AgentDeps`. It must:

- return within `task.budget.max_tool_steps` tool iterations (default 4) and `task.budget.max_tokens`;
- never raise for expected failures (empty retrieval, tool permission denied); it sets `partial=True` and explains in `answer`;
- raise only `BudgetExceeded` or provider errors that the orchestrator's retry policy handles;
- cite evidence: every number taken from a tool result carries a `[tN]` marker and every policy statement a `[dN]` marker; the agent turns markers into `Citation` objects and lists all evidence it was shown in `evidence`;
- pass every model call through the `ContextPolicy` (8.6);
- stream answer text only when the orchestrator supplied `AgentDeps.answer_delta`; a call that carries tools stays non-streaming so tool-call fragments never reach the answer area (8.1);
- when `task.feedback` is set, include it in the prompt as the reason the previous answer was rejected.

Phase 1 agents:

| Agent | Spec | Behavior |
| --- | --- | --- |
| `direct` | cacheable, exclusive | Answers without evidence (greetings, questions about the assistant itself). Its prompt forbids factual claims about orders or policies |
| `docs` | cacheable, needs retriever | Retrieves chunks, answers with document citations. v1 adds query rewriting (9.3) |
| `data` | not cacheable, needs tools | Bounded tool loop over the tenant's MCP tools plus the platform tool `result_page@1`, answers with tool citations |
| `clarify` (v1) | not cacheable, exclusive | Returns one clarifying question; the run ends with status `needs_clarification` |

An agent marks a question-back result with `AgentResult.needs_clarification`, not with a name the orchestrator knows. `MergeAgent` returns the single result unchanged, so an exclusive clarification plan keeps its marker; a merged multi-agent answer never does.

`MergeAgent` is not routable. The orchestrator calls it when a plan has more than one agent; it combines answers, keeps all citations and states which source is missing if a branch was partial.

### 6.5 Fan-out and merge

If a branch raises, its `AgentResult` is replaced by a partial result carrying the error, and the merge agent is told which source is missing (AC-04c). Each branch has its own context: the merge step sees the agents' answers and citations, never the raw rows or chunks. This is context isolation by construction (8.6, layer 3).

### 6.6 Retries and timeouts

| Failure | Policy |
| --- | --- |
| Provider timeout or 429 or 5xx | Retry twice with exponential backoff (1 s, 3 s); then fallback provider if the tenant config names one (v1); then `ProviderUnavailable` |
| Provider 4xx (auth, invalid request) | No retry, fail fast |
| Structured output does not validate | One repair call with the validation error appended |
| Plan does not validate | One repair call, then `limits.fallback_plan` |
| Tool validation error | Returned to the model once as a tool error message so it can fix the arguments |
| Tool execution error | One retry; then the agent continues without that tool result |
| Tool permission denied | No retry; recorded; agent continues |
| Run token rejected by the tool server | No retry; run fails with status `auth_error` (indicates a bug or a clock problem, not a model problem) |
| Whole run | Hard timeout from config (default 60 s); the run ends with status `timeout` and whatever partial answer exists |

### 6.7 Human-in-the-loop (v2)

LangGraph interrupts are placed before any tool call whose `ToolSpec.risk == "write"` or whose name is in the tenant's `tools.ask` list. Permission vocabulary follows the allow, ask, deny convention used by agent products such as Claude Code: `allow` runs, `ask` waits for approval, anything not listed is denied. The run is persisted with status `waiting_approval` and resumed through `POST /runs/{id}/approve` or `/reject`. In Phase 1 and v1 no tool has risk `write` and `ask` is empty, so the hook exists but never fires.

### 6.8 Quality gate and corrective actions (v1)

The gate produces a signal; the orchestrator decides what to do with it. Design rationale and alternatives are in ADR 0013.

**Layer 1: deterministic checks** (v1). Pure functions in `switchboard.gate.checks` over the answer, its citations and the evidence the agent saw. An agent is evidence-based when its `AgentSpec.needs` names `tools` or `retriever`, so the gate never hard-codes agent names:

| Check | Fails when | Corrective action |
| --- | --- | --- |
| `numbers_grounded` | A number in the answer (excluding numbers that appear in the user's query) does not occur in any tool result or chunk the agent saw, after normalization of separators and units | Retry the same agent with feedback naming the unsupported numbers |
| `citations_valid` | An evidence-based answer has no citation, or a marker refers to evidence the agent was not given. When no evidence exists at all the check passes, because `evidence_present` already reports that failure and a citation retry would be the wrong action | Retry the same agent with feedback |
| `evidence_present` | The agent saw no usable evidence at all (empty retrieval, an empty tool result, every tool denied) | Re-plan: the supervisor chooses again with the reason |
| `merge_complete` | A merged answer is missing a source. The check fails whether or not the answer names the missing source | None; the honest partial note is the correct output |

Number normalization removes thousands separators (comma, space, underscore), normalizes decimals, maps the words thousand, million and billion and the attached multipliers k, M and B to plain digits, and keeps percent as a unit, so `1,234` matches `1234`, `1.5 million` matches `1500000`, and `3.5 %` matches `3.5%` but not `3.5`. It does not compute derived numbers (ADR 0013). Evidence carries the text the agent saw in `EvidenceRef.text` and an `empty` flag, so an empty tool result is distinguishable from no evidence.

The aggregate is a `GateVerdict` with the failed check names, a concrete `feedback` string and an `action` of `retry`, `replan`, `none` or `pass`. `evidence_present` takes precedence over a retry; `merge_complete` alone recommends `none`.

The retry is Reflexion-style: the failure message is concrete and comes from an external check, not from the model criticizing itself, because self-critique without an external signal is not reliable. At most one corrective action (retry or re-plan) per run; `attempts` enforces it. A retry re-runs every agent in the current plan with `AgentTask.feedback` set. A re-plan passes the previous plan and the gate reason to the supervisor and requires a plan that differs from it; if the model repeats the plan after its one repair, the configured fallback is used, or a single-agent plan when the fallback is identical to the previous one. The final `RunResult` carries `attempts`, `previous_plan`, `gate` and an `errors` entry of kind `CheckFailed`; the `gate` span records `switchboard.gate.verdict`, `switchboard.gate.failed_checks` (comma-separated) and `switchboard.gate.action`, so both attempts and the failed checks are visible in the trace.

**Layer 2: LLM judge** (v2). Runs only when layer 1 passes. Enabled only after its verdicts are compared against a human-labeled sample and the agreement is reported (T3.08).

The `gate` eval suite (dataset `v2`, 30 cases) labels answers as acceptable or weak and reports precision, recall and F1 of layer 1 per check, so false rejections from `numbers_grounded` (for example a correct derived percentage) are measured rather than assumed away. The labels in that version are constructed and read through by the implementation session, not by an independent human annotator; the report says so. The one deliberately acceptable derived-percentage case makes the known false positive visible.

### 6.9 Agent loop orchestrator (v1)

`AgentLoopOrchestrator` is the second `Orchestrator` adapter and the control group for the routing design (ADR 0002). It has no supervisor and no fan-out: one model gets every tool the tenant may use plus the platform tools `search_docs@1` (wraps the `Retriever`) and `result_page@1`, and loops up to `limits.max_loop_steps` (default 8). It uses the same providers, tool sources, context policy, gate, cache and tracing.

One loop step is one model call. A response with tool calls runs them and continues; a response without one is the answer. Only tools with `risk: read` are exposed, a tool in the tenant's `ask` list is withheld because approval does not exist before v2, and a call the model names that the loop did not expose is answered with a tool error and never executed, so no write tool can run. The loop numbers tool results in call order and uses the same bounded `ToolResultView`, `result_page@1` paging, guardrail wrapping and citation markers as the data agent. The result store is per attempt, so a `result_id` from a rejected attempt cannot leak into the next one. Every step carries tools, so the loop never streams answer deltas (8.1); the step stream still carries `tool.called`, `agent.started` and `agent.finished` events because each executed call runs inside an `agent.data` or `agent.docs` span. The plan is recorded on a `supervisor.plan` span once it is known, after the loop and before the gate, so the trace and the step stream keep the same `plan.decided` event as the LangGraph path. The semantic cache is the same `Cache` port call before the loop and a store after a passing gate (10.1), and a rejected run token ends the run with status `auth_error` (6.6).

The plan does not exist before the run, so it is derived from what the loop actually did: any data tool call adds `data` (including `result_page@1`, which can only page a data result), any `search_docs` call adds `docs`, no tool call gives `direct`, and only tool names the loop exposed count. Because there is no supervisor, a gate corrective action is one more loop with the gate's feedback rather than a re-plan against a previous plan; `attempts` still stops at one, and the run total sums both attempts. The semantic cache is the same `Cache` port call before the loop and a store after a passing gate; the loop cannot reuse the cache embedding, because the model chooses the search query rather than the user's original wording. Both orchestrators pass the same orchestrator contract tests (answer, citations, plan label, status, errors and run span). The comparison report (T2.12) puts plan accuracy, tool-call accuracy, latency, tokens and cost side by side for both orchestrators on both providers.

## 7. Tool layer

### 7.1 MCP server

`mcp_servers/olist` is a FastMCP server (renamed `MCPServer` in the official SDK 2.x) exposed over streamable HTTP. It owns a connection pool to PostgreSQL using the `switchboard_tools` role, which has `SELECT` on the `olist` schema and nothing else. Every tool runs inside `SET TRANSACTION READ ONLY` with `statement_timeout = 5s` and a hard row cap of 200 per result.

Tools are designed to return aggregates first: `orders_late@1` returns counts and averages plus the matching rows, so the model can usually answer from the summary without reading rows.

Phase 1 tools (all `risk: read`, all parameterized, no free-form SQL):

| Tool | Arguments | Tenant scoped |
| --- | --- | --- |
| `orders_summary@1` | `date_from`, `date_to`, `status?` | yes (seller filter) |
| `orders_late@1` | `date_from`, `date_to`, `min_days_late?` | yes |
| `order_lookup@1` | `order_id` | yes |
| `top_products@1` | `date_from`, `date_to`, `metric: revenue or count`, `limit` | yes |
| `describe_schema@1` | none | no |

Every result carries the `audit_id` of its audit row in the MCP result metadata, so tool citations can link to the audit log.

Argument conventions (shared with the toolcall dataset): dates are `YYYY-MM-DD`; `date_to` is inclusive; date ranges filter on the order purchase date for every tool; `status` takes Olist `order_status` values; `metric` is `revenue` (sum of item prices) or `count` (items sold); optional arguments are omitted unless needed.

v2 adds `query_readonly_sql@1`: free-form SELECT validated with sqlglot (single statement, allowed tables only), still inside a read-only transaction, disabled by default in the tenant config.

### 7.2 Tool manifest and versioning

The manifest (`mcp_servers/olist/manifest.yaml`) is the source of truth for `ToolSpec`. The wire name is `name@major`. Rules:

- Adding an optional argument or a result field is a minor change; the wire name stays.
- Removing or renaming an argument, changing a type or changing semantics is a major change; the server serves both majors for one phase and the old one logs a deprecation warning.
- The eval `toolcall` dataset pins tool versions so a major bump cannot silently change scores.

### 7.3 Permissions and run tokens

```yaml
# config/tenants.yaml
tenants:
  default:
    provider: lmstudio
    agents:
      allow: ["direct", "docs", "data"]          # v1 adds "clarify"
    tools:
      allow: ["orders_summary@1", "orders_late@1", "order_lookup@1", "top_products@1", "describe_schema@1"]
      ask: []                                     # v2, human approval
    seller_ids: null          # unrestricted
  seller_a:
    provider: lmstudio
    agents:
      allow: ["docs", "data"]
    tools:
      allow: ["orders_summary@1", "order_lookup@1", "top_products@1"]
    seller_ids: ["955fee9216a65b617aa5c0531780ce60"]
```

Tools not listed under `allow` or `ask` are denied. `ask` is the v2 human-approval list: this phase has no approval hook, so an ask tool is denied and audited like an unlisted one.

`config/tenants.yaml` documents the schema and both processes validate it at startup. The API rejects unknown tenant keys, malformed or duplicate wire names, a tool in both `allow` and `ask`, empty or duplicate `seller_ids`, unknown agent names (resolved through the entry point registry) and unknown tool wire names (resolved through the manifest each tool source declares in `platform.yaml`). The MCP server reads the same file but stays independent of `switchboard.core`: it validates the fields its trust boundary depends on (tool wire names against its own manifest, the ask list and the seller ids) and rejects unknown keys. A typo therefore fails the start of either process instead of surfacing as a silently missing agent or a denied call during a run.

**The tool server must not trust the caller's claim about who it is.** In 0.1 the tenant id travelled in request metadata, so the server-side check compared a claim against a list without knowing whether the claim was true. In 0.2 the API mints a **run token** at the start of every run (ADR 0014):

```json
{"iss": "switchboard-api", "aud": "mcp-olist", "tenant_id": "seller_a",
 "run_id": "6f1c...", "iat": 1762934400, "exp": 1762934490, "jti": "..."}
```

- Signed with HS256 and a shared secret (`SWITCHBOARD_RUN_TOKEN_SECRET`) known only to the API and the tool server.
- Lifetime is the run timeout plus 30 seconds.
- `McpToolSource` sends it as `Authorization: Bearer <token>` on the MCP HTTP transport, alongside the W3C `traceparent` header.
- The server verifies signature, audience and expiry, takes `tenant_id` and `run_id` from the token only, and ignores any tenant or seller field in tool arguments or metadata. It then loads the tenant's allowlist and `seller_ids` from its own copy of the tenant config.

Enforcement still happens twice: `McpToolSource` filters `list_tools` so the model never sees a denied tool (convenience, fewer wasted model turns), and the server checks the allowlist on every call (the trust boundary). Tenant-scoped tools receive `seller_ids` from server-side config, never from model-generated arguments.

With several tool servers, verification would move into a gateway in front of them (the approach of MCP gateways such as IBM's ContextForge). With one server, a gateway is an extra process without a second consumer, so the check stays in the server.

### 7.4 Audit

The MCP server writes one row to `tool_audit` per call before returning (tenant id and run id from the token, tool, version, SHA-256 of canonical JSON args, redacted args, status, duration). The audit write is inside the tool span so a failed audit write fails the call. Denied calls and rejected tokens are audited too.

### 7.5 Platform tools

Some tools belong to the platform, not to a data source. They are served by an `InProcessToolSource` scoped to the run, are read-only, and are traced like MCP tools:

| Tool | Purpose | Phase |
| --- | --- | --- |
| `result_page@1` | `result_id`, `offset`, `limit`: returns further rows of a tool result already fetched in this run (8.6) | Phase 1 |
| `search_docs@1` | `query`, `k`: wraps the `Retriever`, used by the agent loop orchestrator | v1 |

### 7.6 Why there is no sandbox

Products such as Codex and Claude Code run model-written shell commands, so they need a sandbox (filesystem and network isolation, resource limits). Switchboard's tools are fixed functions whose arguments are validated against a schema; the model cannot execute code. The equivalent containment is already in place: a SELECT-only role, read-only transactions, statement timeout, row cap, a separate process and an audit log. A sandbox becomes necessary only if a tool that executes model-written code is added (for example Python for charts); that would need its own ADR.

## 8. Model layer

### 8.1 Provider adapter

`OpenAICompatibleProvider` wraps the official `openai` Python client with a configurable `base_url`, API key and model name. It covers LM Studio (`http://host.docker.internal:1234/v1`), DeepSeek and later any compatible gateway. `capabilities()` is declared in config per model, not probed, because small local models often accept a `tools` parameter and then ignore it.

```yaml
# config/providers.yaml
providers:
  lmstudio:
    adapter: openai_compatible
    base_url: ${LMSTUDIO_BASE_URL}
    api_key: lm-studio
    model: ${LMSTUDIO_MODEL}
    capabilities: {tools: true, json_schema: false, streaming: true, reasoning: false, context_window: 8192}
    extra_body: {reasoning_effort: none}   # disable Qwen3 thinking on LM Studio
  deepseek:
    adapter: openai_compatible
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY:-}
    model: deepseek-flash
    tool_name_pattern: "^[a-zA-Z0-9_-]+$"  # @major would be rejected; mapped to _major
    capabilities: {tools: true, json_schema: false, streaming: true, reasoning: true, context_window: 1000000}
    extra_body: {reasoning_effort: none}   # thinking is on by default; plans stay non-thinking
  mock:
    adapter: mock
    model: ${LMSTUDIO_MODEL}
    fixtures: evals/fixtures/mock_provider
embedders:
  lmstudio:
    adapter: openai_compatible
    base_url: ${LMSTUDIO_BASE_URL}
    model: ${LMSTUDIO_EMBEDDING_MODEL}
    dimension: 1024
  mock:
    adapter: mock
    dimension: 1024
```

Prices live in `config/prices.yaml` (12.4), not here.

The DeepSeek rows were verified on 2026-11-12 against the provider's price page and chat-completions reference: `deepseek-flash` and `deepseek-v4-pro` accept tools and streaming, the context window is 1M, thinking is on by default, and `response_format` accepts `json_object` but not a JSON schema, so `json_schema` is false and the adapter uses the forced tool-call strategy. `reasoning_effort: none` keeps the model non-thinking, which the forced tool-call strategy needs because `tool_choice: "required"` is rejected in thinking mode. The key is written as `${DEEPSEEK_API_KEY:-}`: an unset key is an empty value, so all four config files still load for a local or mock run, and only building the `deepseek` provider (a tenant or an eval that selects it, and `--record` through it) fails, with a named `ConfigError`, before any request. A literal placeholder such as LM Studio's `lm-studio` is still written directly.

`tool_name_pattern` is an optional provider-level regex. When set, the adapter maps each wire name to a name that matches the pattern (an invalid character becomes `_`, with a numeric suffix on collision), sends the safe name in the tool definition and in prior assistant tool-call messages, and maps the model's returned name back to the wire name. The tool, audit row, trace and result keep the wire name; a null pattern keeps the previous behavior. DeepSeek needs it because its function-name pattern rejects `@`, which every `name@major` wire name contains.

**Streaming answer tokens (v1).** When `CompletionRequest.stream` is true and the declared capability allows it, `OpenAICompatibleProvider` passes `stream: true` and `stream_options: {include_usage: true}` to the SDK and consumes the async iterator. It forwards each answer text fragment to `CompletionRequest.delta_callback`, accumulates the final text, keeps reasoning fragments (`reasoning_content` and the other fields of 8.3) in `reasoning` and never sends them to the callback, merges tool-call fragments by `index` and parses the arguments exactly as in the non-streamed path, and reads usage from the chunk that carries it. A gateway that sends no usage chunk leaves usage at zero rather than guessing. The returned `CompletionResponse` has the same shape as a non-streamed call, so agents and the gate do not branch on how it was produced. Structured-output calls are never streamed: a request with `stream` and a `response_schema` is rejected, and the supervisor never asks for one. `MockProvider` replays a recorded answer as deterministic fixed-size fragments, so streaming tests need no network. Non-streaming behaviour is unchanged.

**Phase 1 local models** (decided 2026-11-12, to be confirmed by the live smoke in T1.02). The development machine has an 8 GB laptop GPU and 32 GB RAM, and must hold a chat model and an embedding model at the same time.

| Role | Model | Why |
| --- | --- | --- |
| Chat | Qwen3-8B, Q4_K_M | Documented tool calling, supported by LM Studio, largest size that fits next to the embedder. Fallback: Qwen3-4B if VRAM or latency is not acceptable |
| Embedding | bge-m3, 1024 dimensions | Multilingual, so a question in Chinese can retrieve English documents (9.3) |

The VRAM budget (about 5 GB of weights, about 1.2 GB of KV cache at 8k context, about 0.6 GB for the embedder) is an estimate; T1.02 records the measured usage. The embedding dimension fixes the `vector(1024)` column type; changing the embedding model later needs a migration and a re-ingest.

### 8.2 Structured output strategy

In order of preference, chosen by `capabilities.json_schema`:

1. Native JSON schema response format.
2. Tool-call trick: declare one tool whose input schema is the target schema and force its use.
3. Prompted JSON: schema in the system prompt, parse the first JSON object in the reply, one repair round on validation failure.

The chosen strategy is recorded as a span attribute so eval reports can break down accuracy by strategy.

### 8.3 Tool call normalization

Tool calls from any provider are normalized to `ToolCall(name, arguments: dict, call_id)`. Arguments that arrive as a JSON string are parsed; failures become a tool validation error returned to the model (6.6).

Reasoning output is separated from the answer: `<think>` blocks in the text (Qwen3 emits them by default) or a separate reasoning field are removed from `text` before parsing, and only their length is recorded on the span. Plans and other structured calls request non-thinking mode where the model supports a switch (Qwen3: `/no_think` in the system prompt), because thinking tokens add latency without improving a one-word classification.

Usage is normalized the same way: cached input tokens are read from whichever field the provider returns (for example `prompt_cache_hit_tokens` on DeepSeek, `prompt_tokens_details.cached_tokens` on OpenAI-style responses) and default to zero when absent, as with LM Studio.

### 8.4 Fallback and routing by policy (v1, v2)

v1: a provider may name one `fallback_provider` in `providers.yaml`; it is used after the retry budget of the primary is exhausted. Provider level, not tenant level, because the provider object and the orchestrator that holds it are built once per provider name, so two tenants with the same provider cannot silently get different fallbacks. Only transient transport failures (timeout, connection error, 429 or 5xx) are retried (default twice, 1 s and 3 s); a non-retryable request error such as auth failure or malformed request fails fast and is never sent to the fallback. Both providers need a price row, so a run that used the fallback is still costed exactly (12.4). The eval runner builds the named provider without the fallback, so a comparison run cannot silently score a second provider. v2: a `ModelRouter` policy chooses a provider per call based on purpose (plans on a cheap fast model without reasoning, final answers on a stronger model, optionally with reasoning when `capabilities.reasoning` is true), with cost and latency ceilings from the tenant config. Both are implemented as a `ModelProvider` decorator so agents do not know about them.

### 8.5 Prompt layout for provider prefix caching

Most providers cache identical prompt prefixes and bill cached input tokens at a lower rate (DeepSeek does this by default). This is the cheapest cost reduction available and needs no infrastructure, only discipline in how prompts are built. Every prompt builder in `agents/prompts/` assembles messages in this order:

1. Static system prompt for the agent (versioned file, byte-identical across runs).
2. Tool definitions, sorted by wire name.
3. Static instructions about data blocks and citation markers.
4. Tenant-specific facts (available collections, tool notes).
5. Conversation history (v1).
6. The current query, then evidence blocks.

Nothing dynamic (timestamps, run ids) appears before step 4. A unit test asserts that two runs with different queries produce byte-identical prefixes up to step 3. Every model span records `switchboard.usage.cached_input_tokens`, and reports show the cached share of input tokens per provider.

### 8.6 Context management

Rationale and alternatives in ADR 0012. Layers, cheapest and least lossy first:

| Layer | Mechanism | Phase |
| --- | --- | --- |
| 1. Shape at the source | Tools return aggregates first; MCP row cap 200 | Phase 1 |
| 2. Bounded view plus reference | The agent stores the full result in a run-scoped result store and gives the model a `ToolResultView`: summary, total row count, at most `limits.model_view_rows` rows (default 20), `truncated` flag and `result_id`. The model calls `result_page@1` if it needs more | Phase 1 |
| 3. Context isolation | Each agent has its own context; merge sees answers, not raw evidence (6.5) | Phase 1, by design |
| 4. Clear old tool results | `ContextPolicy.fit` estimates tokens before each model call; above `limits.context_fill` (default 0.7) of `context_window` minus `max_tokens`, it replaces the oldest consumed tool results with a one-line placeholder (`[result r_7f3a: 1834 rows, cleared]`), keeping the tool call itself. Never touches the system prompt or the latest result | Phase 1 |
| 5. Model summarization | Not implemented. Lossy, costs a call, invalidates the provider prefix cache. Revisited only if an eval shows that layers 1 to 4 lose answers | not planned |

The default policy `ClearOldestToolResults` lives in core as a pure function. Token estimation in core uses a conservative character heuristic (core has no tokenizer dependency); the provider's reported usage is recorded afterward so the estimate's error is visible in traces. Spans record `switchboard.context.tokens_estimated`, `switchboard.context.compacted` and `switchboard.context.cleared_results`.

Full tool results are kept for the life of the run in the result store and written to the tool span as an event, capped at 64 KB, so the trace can show what the model did not see.

## 9. Knowledge layer

### 9.1 Ingestion

`switchboard ingest <path> --tenant default` reads markdown and text files, splits on headings then on a token window (512 tokens, 64 overlap), embeds with the configured `Embedder`, and writes `documents` and `chunks`. Content hash per document prevents duplicate work. After a successful ingest the tenant's `doc_index_version` is incremented, which invalidates the semantic cache for that tenant (10.3).

Demo corpus: synthetic policy documents written for this project (returns, shipping, seller onboarding, payment methods, late delivery compensation), clearly marked as synthetic. See ADR 0010.

### 9.2 Retrieval

Phase 1: cosine similarity on pgvector (HNSW index), top k. The query embedding computed for the cache lookup is reused, so a `docs` run embeds the query once.

v1: hybrid search, selected by `retrieval.search` per tenant (`vector` keeps the Phase 1 path, `hybrid` adds full text). Full-text search runs `websearch_to_tsquery('english', query)` through `ts_rank` on the generated `chunks.tsv` column and its GIN index, over the same tenant filter as the vector branch. Each branch returns `max(k, 20)` candidates, so k can still be 50 for a corpus dump. The two rankings are combined with reciprocal rank fusion (`1 / (60 + rank)`, rank starting at 1); a chunk found by both branches therefore beats a chunk found by one. Ties break by cosine score and then chunk id, so a result list is deterministic.

`RetrievedChunk.score` stays the cosine similarity, including for a chunk that only the text branch found (the text query selects the distance too). RRF decides the order; the cosine score is what `retrieval.min_score` compares. The optional re-ranking of the top 20 by a local cross-encoder is not enabled in T2.03: it would add sentence-transformers and a torch stack on the 8 GB GPU shared with qwen3-8b and bge-m3, and no measured benefit justified that cost. The row stays in section 21 as optional.

### 9.3 Query rewriting (v1)

Rewriting costs a model call, so it is triggered by conditions, not applied to every query:

```
history present?       yes: contextualize (rewrite into a standalone question)
                       no:  use the query as written
retrieve
evidence sufficient?   top score >= retrieval.min_score: done
                       otherwise: one rewrite round (terms and language of the corpus,
                       or a hypothetical answer passage, HyDE) and retrieve again
```

At most two retrieval rounds per `docs` run, and at most two rewrite calls: one to contextualize history before the first round, plus one after a weak first round. `retrieval.rewrite` enables rewriting for a tenant; `retrieval.rewrite_mode` is `conditional` for the docs agent and `always` only for the rag eval, which compares rewriting against the query as written. The strategy helper returns the chunks, the queries it actually used, the rewrite and retrieval round counts and the rewrite usage, so the eval can report cost and benefit separately. A rewrite that fails (provider error or an empty answer) keeps the previous query and records the failure on the run instead of losing the retrieval result.

Cases that need rewriting: follow-up questions with references ("and furniture?"), vocabulary or language mismatch with the corpus (a question in Chinese against English documents), and multi-part questions (decomposed into one query per part). The `rag` suite reports recall@5 for: query as written, always rewrite, conditional rewrite, and conditional rewrite plus hybrid search, so the README can show whether rewriting pays for itself. Those four names are `query_as_written`, `always_rewrite`, `conditional_rewrite` and `conditional_hybrid` in the report.

### 9.4 Chunk and tool result format to the model

Retrieved chunks and tool results are passed as data blocks with citation ids:

```
<document ref="d1" id="chunk_8812" source="returns-policy.md">
...text...
</document>
<tool_result ref="t1" tool="orders_late@1" result_id="r_7f3a">
{...ToolResultView JSON...}
</tool_result>
```

with a system instruction that content inside `document` and `tool_result` blocks is untrusted data and never an instruction (13.1), and that claims taken from a block must carry its `ref` marker.

When the detector in 13.1 matches, the opening tag gains `flagged="true"` and the body starts with one short note that names the pattern ids. The content itself is not changed, and the `ref`, `id` and `result_id` format stays the same, so citations and the gate keep working on flagged blocks.

## 10. Semantic cache (v1)

### 10.1 Placement and scope

The lookup happens **before** the supervisor. A hit therefore skips every model call, and the cost of a hit is the query embedding only (AC-10d). The embedding is reused by retrieval on a miss.

Only answers produced by cacheable plans are stored: a plan is cacheable when every agent in it has `spec.cacheable` (in Phase 1 agents: `direct` and `docs`). `data` answers depend on parameters such as dates where two near-identical questions have different answers, so any plan containing `data` is never stored. Lookups are skipped when the session has history (v1), because a follow-up's meaning depends on the conversation.

### 10.2 Key and match

`CacheKey = (tenant_id, normalized_query, embedding, index_version)`. The route is not part of the key: it is produced by a nondeterministic model call that a pre-routing lookup has not made yet. The stored entry records the plan label that produced it, so a hit shows "cached answer from plan docs" in the trace. The adapter's `key_for(ctx, query)` builds the key: it normalizes the query, embeds the raw query with the configured embedder and reads the tenant's current `doc_index_version`. A miss keeps the embedding and the orchestrator passes it to the docs agent in `AgentTask.query_embedding`, so retrieval does not pay for a second embedding. The `none` adapter returns an empty embedding and never embeds.

A hit requires same tenant, same index version, not expired, and cosine similarity at or above the threshold. The threshold default is 0.92 but is tuned on the `cache` suite: paraphrase pairs (should hit) and near-miss pairs (must not hit), including cross-type near misses where one query needs data and the other is a policy question ("late delivery compensation policy" versus "late deliveries in March"). The report shows hit rate and false-hit rate for several thresholds; the chosen value and the report path go into the config comment. Measured on 2026-11-13 with bge-m3 and dataset v5, raw cosine similarity separates the two classes poorly: at 0.94 the cache hits 1 of 25 paraphrases and false-hits 1 of 25 near misses (0.04 each, accuracy 0.50), and at 0.99 it hits neither. The demo tenants therefore use the 0.94 threshold but keep `cache.enabled: false`; the report and the config comments carry the numbers, and turning it on is one config value.

### 10.3 Invalidation

Entries carry the `index_version` at store time. Ingestion bumps the tenant's version. A lookup compares the entry against both the key's version and the tenant's current version, so every older entry stops matching immediately; the next store for that tenant deletes rows of other versions and expired rows in the same transaction. A TTL (default 7 days) covers `direct` answers that do not depend on documents. `invalidate(tenant_id)` deletes every entry of a tenant and returns the count; the adapter exposes it for an operator or a future night job, while the version check is what makes an ingest safe without a job.

### 10.4 Measurement

Each lookup creates a `cache.lookup` span with `switchboard.cache.hit`, `switchboard.cache.similarity` and `switchboard.cache.threshold`; a store creates `cache.store` with the plan and TTL and `switchboard.cache.stored`. Runs record `cache_hit`. A hit returns the stored answer with zero usage and cost, because no chat call was made; the query embedding is the only cost and the local embedder has no price row.

## 11. Multi-tenancy

Logical isolation, enforced at every layer:

| Layer | Isolation mechanism |
| --- | --- |
| API | `tenant_id` required on every request; unknown tenant is a 404. Not authenticated: the demo trusts the caller (requirements, non-goals) |
| Config | `TenantContext` resolved once per run from `config/tenants.yaml` |
| Agents | Supervisor only offers the tenant's allowed agents |
| Tools | Allowlist on client and server; server takes the tenant from the signed run token; seller filter injected server-side |
| Retrieval | `WHERE tenant_id = $1` on every chunk query; tests assert no cross-tenant chunk ever appears |
| Cache | tenant id in the key |
| Traces and runs | tenant id attribute on every span; `GET /runs`, `GET /runs/{id}` and `GET /runs/{id}/trace` require `tenant_id` and filter on it, and another tenant's run answers 404 like a missing one |
| Audit | tenant id column, taken from the token |
| Sessions | the history query filters on `tenant_id` and `session_id` together, so turns never cross a tenant or a session; a reused session id under another tenant reads an empty history |

The tenant schema (AC-11a):

| Key | Meaning |
| --- | --- |
| `provider` | A provider name from `providers.yaml`; fails at startup when unknown |
| `agents.allow` | Registered agent names the supervisor may plan with; at least one |
| `tools.allow` | Tool wire names callable without approval |
| `tools.ask` | Tool wire names that would need human approval in v2; denied in this phase |
| `seller_ids` | `null` for every seller (the unrestricted demo tenant) or a non-empty list injected server-side into every tenant-scoped query |
| `retrieval` | `k` (5), `min_score` (0.35), `rewrite` (false), `rewrite_mode` (`conditional` or `always`), `search` (`vector` keeps the Phase 1 path, `hybrid` adds full text and RRF) |
| `cache` | `enabled` (false), `threshold` (0.92 default; the demo uses 0.94 from the cache report), `ttl_days` (7); implemented in v1 (10) |

Unknown keys, malformed or versionless wire names, duplicates, a tool in both `allow` and `ask`, and an empty `seller_ids` list all fail at startup rather than during a run. Agent names are checked against the entry point registry and tool wire names against each tool source's manifest (`platform.yaml`), which the MCP server also reads to validate its own copy of the tenant file.

The demo gives `seller_a` the same five synthetic policy documents as `default` (29 chunks) so retrieval isolation is tested against two real indexes:

```bash
uv run --env-file .env switchboard ingest data/documents --tenant seller_a
```

Physical isolation (schema per tenant, separate databases) is explicitly out of scope and noted as a v2+ option in the review.

## 12. Observability

### 12.1 Instrumentation

The code uses the OpenTelemetry API directly. Span names and attributes follow the GenAI semantic conventions where they exist and a `switchboard.*` namespace otherwise.

| Span | Kind | Key attributes |
| --- | --- | --- |
| `run` | server | `switchboard.tenant_id`, `switchboard.run_id`, `switchboard.orchestrator`, `switchboard.plan`, `switchboard.cache.hit`, `switchboard.attempts`, `switchboard.status` |
| `supervisor.plan` | internal | `switchboard.plan`, `switchboard.plan.size`, `switchboard.plan.reason`, `switchboard.plan.fallback` |
| `agent.<name>` | internal | `switchboard.agent`, `switchboard.partial`, `switchboard.citations` |
| `chat <model>` | client | `gen_ai.operation.name=chat`, `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `switchboard.usage.cached_input_tokens`, `switchboard.structured_output.strategy`, `switchboard.context.tokens_estimated`, `switchboard.context.compacted`, `switchboard.context.cleared_results`, `switchboard.latency_ms`, `switchboard.cost_usd` |
| `embeddings <model>` | client | `gen_ai.operation.name=embeddings`, token usage |
| `execute_tool <name>` | client | `gen_ai.tool.name`, `switchboard.tool.version`, `switchboard.tool.args_hash`, `switchboard.tool.status`, `switchboard.tool.total_rows`, `switchboard.tool.audit_id`, guardrail attributes below; event `tool.result` with the full result capped at 64 KB |
| `retrieve` | internal | `switchboard.retrieval.k`, `switchboard.retrieval.strategy`, `switchboard.retrieval.hits`, `switchboard.retrieval.round`, `switchboard.retrieval.top_score`, guardrail attributes below |
| `cache.lookup` | internal | see 10.4 |
| `gate` | internal | `switchboard.gate.verdict`, `switchboard.gate.failed_checks`, `switchboard.gate.action` |

`execute_tool <name>` exists twice per call: the client span in `McpToolSource` carries the network timing and the tool attributes, and the server span inside the MCP process continues the trace from the `traceparent` header. The server writes the `tool.result` event because it is the process that holds the full result. `switchboard.cost_usd` appears once `config/prices.yaml` exists (section 12.4, v1); until then the attribute is absent rather than zero for a paid model.

The guardrail attributes on `retrieve` and `execute_tool` come from `core/guardrails.py`: `switchboard.guardrail.flagged`, `switchboard.guardrail.patterns` (comma-separated pattern ids, empty when clean), `switchboard.guardrail.pattern_set` (the pattern set version) and `switchboard.guardrail.match_count`. Only ids and counts are stored; the matched text and the full untrusted content are never written to a span.

### 12.2 Export, storage and read-after-run consistency

A custom `PostgresSpanExporter` (batched through the SDK's `BatchSpanProcessor`) writes spans to the `spans` table. Because the API, the orchestrator and the eval CLI are one process, there is no collector in Phase 1. The MCP server runs its own exporter with the same table and continues the run's trace from the `traceparent` header, so tool spans attach to the run's trace. v1 adds an OTLP exporter and a Jaeger container to show that the same spans render in a standard tool (one config switch).

`config/platform.yaml` selects the exporter: `postgres` (the default), `otlp` or `none`. The API and the MCP server both resolve it through `switchboard/tracing/config.py` before they build their tracer provider, so one file drives both processes; the committed value is `${SWITCHBOARD_TRACING_EXPORTER:-postgres}` so the compose profile can switch both without editing a tracked file. `otlp` builds an OTLP/HTTP span exporter bound to `OTEL_EXPORTER_OTLP_ENDPOINT` (a base URL such as `http://jaeger:4318` is normalized to `/v1/traces`) and replaces the PostgreSQL exporter for that process: the trace page reads the `spans` table, so it is empty while OTLP is selected and Jaeger is the trace view. Selecting `otlp` does not make startup depend on the collector: the batch processor exports in the background and retries, so the API and the MCP server start with Jaeger absent. `none` leaves the OTel API a no-op. The exporter choice is never inferred from the presence of Jaeger.

Batch export is asynchronous, so a trace read immediately after a run could be incomplete. Three rules prevent that:

1. The `runs` row is inserted with status `running` when the run starts, before any span can reference it.
2. `spans.run_id` is indexed but has no foreign key, so spans from the MCP process can never fail on ordering.
3. The API calls `force_flush()` on the tracer provider (2 s timeout) before returning the final response or the `run.finished` event; the MCP server flushes at the end of each tool call. If the flush times out, the API writes `runs.trace_complete = false` and the trace API marks the trace as `incomplete` instead of returning a silently partial tree.

### 12.3 Trace API and UI

`GET /runs/{id}/trace` assembles the span tree for the run. The web UI's trace page has three panes: run list (left), the run's span tree with duration bars (center), and the selected span's attributes, prompt excerpt, tool arguments and full tool result (right). Citation chips in the chat answer open the trace at the cited tool span or chunk; a tool chip also shows the audit row. v1 replaces the tree with a timeline and adds a graph view of the plan. The visual design pass is a v2 task.

The trace page can replay a recorded run on another orchestrator (T3.07): its Replay panel reads `available_orchestrators` from `GET /settings`, sends the run's query with `orchestrator` set, and shows the new run's status with links to its trace and run detail. The original run is read, never rewritten, and the replay starts without the original session history.

The author's `langgraph-visualizer` project can be attached as an optional development tool: the LangGraph adapter exposes its compiled graph so `watch(graph)` can wrap it. It is not part of the platform's trace path because it is specific to LangGraph and the platform's trace must survive an orchestrator swap.

### 12.4 Cost accounting (v1)

`config/prices.yaml` holds input, cached input and output prices per million tokens per provider and model, with a `verified_on` date and the source URL. Values are copied from the provider's price page when a comparison run is prepared, never from memory. A row with a nonzero price and no source URL fails validation, so an unverified number cannot sit in the table. A row with `model: null` applies to every model of that provider (the local zero row); a matching model row wins over it. The schema is `PricesConfig` and `PriceEntry` in `core/config.py`, loaded by `load_settings` like the other three files.

The math is `core/pricing.py`, a pure module: `compute_cost(usage, entry)` bills `input_tokens - cached_input_tokens` at the input rate, `cached_input_tokens` at the cached rate and `output_tokens` at the output rate, per million tokens. It is the single implementation used by the provider adapter, the orchestrator and the report writer. A provider with no row yields `None`: the chat span omits `switchboard.cost_usd` rather than writing zero, and a run or report stores null cost. A local model such as LM Studio has an explicit zero row, so its span, run and report say 0.0 and the report labels it "no API cost" rather than "free".

The provider adapter resolves its row once at construction (through `create_provider`) and writes the per-call cost on the `chat <model>` span and in `CompletionResponse.cost_usd`. Agents sum the responses they received into `AgentResult.cost_usd`, the orchestrator sums the supervisor call and every agent invocation (including a first attempt the gate rejected, a re-plan and the conditional retrieval rewrites), and the run total is that per-call sum. A run that uses the fallback provider therefore stays exact although it mixed two price rows; a call the provider could not price leaves None, and the orchestrator computes from the summed usage and its one price row only when no call reported a cost (older adapters and tests). The summed usage still counts every model call the run made, so a discarded attempt is visible in tokens even when its cost is zero. The API persists the total in `runs.cost_usd` and records the tenant's configured provider in `runs.provider` so the number can be grouped; a fallback call's own provider is on its chat span.

`GET /runs?tenant_id=...&group_by=day|provider` groups the tenant's runs into UTC days or providers and returns, per bucket, the run count, input, cached input and output tokens, the summed `cost_usd` and `unpriced_runs`. The sum is null when no run in the bucket is priced, and `unpriced_runs` counts the null-cost rows, so an aggregate never passes an incomplete sum off as the full cost. Rows written before the `provider` column existed group as `unknown`. Without `group_by`, `GET /runs` keeps the run-list shape.

DeepSeek publishes peak and off-peak rates (off-peak is half). The committed rows carry the peak rate so a recorded cost cannot be lower than the invoice; the row notes say so. The page listed `deepseek-flash` and `deepseek-v4-pro` on the verification date and no longer lists `deepseek-chat`, so only the two listed names have rows.

### 12.5 Step event stream

`POST /chat/stream` returns server-sent events. The events come from a `StepEventProcessor`, an OTel `SpanProcessor` that publishes selected span starts and ends to a per-run in-process queue. The trace and the live view therefore come from one source of instrumentation.

| Event | Emitted on | Payload |
| --- | --- | --- |
| `run.started` | `run` start | run id |
| `cache.checked` | `cache.lookup` end (v1) | hit, similarity |
| `plan.decided` | `supervisor.plan` end | agents, reason |
| `agent.started`, `agent.finished` | `agent.<name>` start and end | agent, partial |
| `tool.called` | `execute_tool` end | tool, status, total rows, duration |
| `gate.checked` | `gate` end (v1) | verdict, action |
| `answer.delta` | provider answer stream (v1) | text fragment |
| `run.finished` | after flush | final payload as in `POST /chat` |

v1 ships answer token streaming. The orchestrator is built with a run-scoped delta sink (in the API, `StepEventProcessor.publish_answer_delta`), puts it in `AgentDeps`, an agent attaches it to an answer call, and the provider calls it for each fragment. Only agent answer calls without tools stream: supervisor plans and tool-call rounds stay non-streaming. Eval runs and unit tests leave the sink unset, so their provider calls and results do not change. `run.finished` is always the last event and its final answer replaces the streamed text in the UI; fragments are live only and are not stored in the trace, so the final answer and its citations remain the source of truth. The final payload carries the run status, so a clarification run ends with `needs_clarification` and its answer is the question, which the chat page renders as a clarification rather than an answer.

## 13. Security

### 13.1 Prompt injection in tool results and documents

- All external content reaches the model inside `tool_result` or `document` blocks with an id and a system instruction that such content is data.
- v1 adds a detector in `core/guardrails.py` with a versioned pattern set (instruction-like phrases, role impersonation, requests to call tools or reveal prompts, encoded payloads). Flagged content is wrapped with `flagged="true"` and a note; the retrieve and tool spans record the pattern ids and the pattern set version. Detection quality is measured by the `injection` suite; patterns are extended from failures, not from guesses.
- Tool arguments produced by the model never include tenant identity or seller ids; those come from the signed run token and server-side config.

### 13.2 Secrets

API keys and `SWITCHBOARD_RUN_TOKEN_SECRET` come from environment variables through `.env` (git-ignored) with an `.env.example`. A key that a local-only setup does not need is written as `${VAR:-}`, so the config loads without it and only building that provider fails, with a named error, before a request (8.1). The compose file refuses to start the API or the tool server if the token secret is missing or shorter than 32 bytes. No secret or token is logged; the provider adapter redacts `Authorization` headers in debug logs. v2 moves cloud secrets to Azure Key Vault.

### 13.3 Audit

See 7.4. The audit table is append-only (no UPDATE or DELETE grant for any application role).

### 13.4 Trust boundaries

| Boundary | What crosses it | Protection |
| --- | --- | --- |
| Client to API | query, tenant id | Input validation only; no end-user authentication (demo non-goal) |
| API to tool server | tool calls | Signed run token, server-side allowlist and seller filter, audit |
| Tool server to database | SQL | SELECT-only role, read-only transaction, timeout, row cap |
| Tool results and documents to model | untrusted text | Data blocks, injection detector (v1), evidence checks (v1) |
| API to model provider | prompts | Secrets in env, no tenant secrets in prompts |

## 14. Data model

```mermaid
erDiagram
  tenants ||--o{ runs : has
  tenants ||--o{ documents : owns
  documents ||--o{ chunks : contains
  runs ||--o{ feedback : receives
  tenants ||--o{ cache_entries : caches
  eval_runs {
    uuid id PK
    text suite
    text dataset_version
    text orchestrator
    text provider
    text model
    jsonb metrics
    text report_path
    timestamptz created_at
  }
  tenants {
    text id PK
    text name
    jsonb config
    int doc_index_version
  }
  runs {
    uuid id PK
    text tenant_id FK
    text session_id
    text query
    text orchestrator
    text provider
    text plan
    text answer
    jsonb citations
    jsonb errors
    text status
    bool cache_hit
    int attempts
    int input_tokens
    int cached_input_tokens
    int output_tokens
    numeric cost_usd
    timestamptz started_at
    timestamptz ended_at
    bool trace_complete
  }
  spans {
    text span_id PK
    text trace_id
    text parent_span_id
    uuid run_id
    text name
    text kind
    timestamptz start_time
    timestamptz end_time
    text status
    jsonb attributes
    jsonb events
  }
  documents {
    uuid id PK
    text tenant_id FK
    text source
    text title
    text content_hash
    timestamptz ingested_at
  }
  chunks {
    uuid id PK
    uuid document_id FK
    text tenant_id
    int ordinal
    text text
    vector embedding
    tsvector tsv
  }
  cache_entries {
    uuid id PK
    text tenant_id FK
    text plan
    text query_text
    vector embedding
    jsonb answer
    int index_version
    int hits
    timestamptz created_at
    timestamptz expires_at
  }
  tool_audit {
    uuid id PK
    uuid run_id
    text tenant_id
    text tool_name
    text tool_version
    text args_hash
    jsonb args_redacted
    text status
    int duration_ms
    timestamptz created_at
  }
  feedback {
    uuid id PK
    uuid run_id FK
    text tenant_id
    smallint rating
    text note
    timestamptz created_at
  }
```

`spans.run_id` and `tool_audit.run_id` are indexed without a foreign key (12.2). Run status values: `running`, `ok`, `partial`, `needs_clarification`, `timeout`, `provider_error`, `auth_error`, `waiting_approval` (v2).

The Olist tables live in a separate schema `olist` loaded from the public CSV files. Two database roles: `switchboard_app` (read and write on the platform tables, no access to `olist`) and `switchboard_tools` (SELECT on `olist`, INSERT on `tool_audit` and `spans`, nothing else). The tool role has no SELECT on `spans`, so the exporter's insert uses `ON CONFLICT DO NOTHING` without a conflict target.

`GET /audit` reads `tool_audit` through `switchboard_app`. `tenant_id` is required and applied in SQL, so another tenant's rows are invisible; `run_id`, `tool_name`, `status`, `from`, `to`, `limit` (1 to 200, default 50) and `offset` narrow the rows, which are ordered newest first. The response carries the stored redacted arguments only, and the API applies the manifest's `sensitive_args` again on read, so a raw sensitive argument cannot leave the process (AC-18a, AC-18b).

Feedback is append-only. `POST /runs/{id}/feedback?tenant_id=<id>` takes `{"rating": -1 | 1, "note": "optional, at most 2000 characters"}` and stores one row with the run's own tenant; zero, every other rating and a boolean or a string are rejected with 422. The run is read tenant-scoped before the insert, so another tenant's run is a 404 and never receives a rating, and a composite foreign key on `(run_id, tenant_id)` refuses a mismatched pair even outside the route. There is no update and no delete route: 405 is the only answer. The newest row is a run's verdict, `GET /runs/{id}` returns it as `latest_feedback`, and the run list does not load it, so listing stays one query. `switchboard_app` has SELECT and INSERT on `feedback` and nothing else, and `switchboard_tools` has no access.

`GET /runs` accepts an optional `session_id` beside `tenant_id` and `limit`, filtered in SQL together with the tenant. It is how the chat page rebuilds a stored conversation from real run rows (newest first, the page reverses) instead of keeping browser history, and a session id from another tenant returns no rows. The route order and the unfiltered list shape are unchanged.

`GET /settings?tenant_id=...` returns the read-only runtime snapshot the settings dialog shows (T3.03): the platform choices (orchestrator, retriever, embedder, cache adapter, context policy and limits), the tenant's provider name, model, capabilities and price row, the tenant's allowlists, seller scope, retrieval and cache settings, every tool spec the configured manifests declare, and every agent spec the entry point registry exposes. `allowed` on a tool or an agent is true only when that tenant's allowlist names it. `available_orchestrators` lists the orchestrator names the process actually built for the tenant's provider (the platform default plus any replay target, T3.07), which is what the trace page's replay selector offers; a target skipped at startup does not appear. The snapshot is built once in `build_app_deps` from the same `Settings`, `Registry`, manifests and built orchestrators the routes use, so it cannot disagree with what runs, and there is no write route. It never contains an API key, a base URL, a database URL or the run token secret; an application built without a snapshot (a test that injects its own `AppDeps`) answers 503 rather than inventing values, and an unknown tenant is a 404.

`cache_entries` (T2.18, `docker/postgres/init/06-cache.sql`) stores one answer per cacheable plan: tenant, plan label, normalized query text, a `vector(1024)` embedding, the serialized `AgentResult`, the index version at store time, a hit counter and the expiry. The HNSW index uses cosine distance, the same operator family as the chunk index; a tenant and version index narrows the lookup and an expiry index supports pruning. The application role has SELECT, INSERT, UPDATE and DELETE, because it increments `hits`, prunes older versions on a store and may invalidate a tenant; the tool role has nothing. The table has a foreign key to `tenants`, so an entry always belongs to a real tenant.

## 15. Error handling

Error taxonomy in `core/errors.py`:

| Error | Retryable | Raised by | Handled by |
| --- | --- | --- | --- |
| `ProviderTransientError` | yes | provider adapter | retry policy, then fallback |
| `ProviderRequestError` | no | provider adapter | run fails with status `provider_error` |
| `StructuredOutputError` | once (repair) | provider adapter | repair call, then the agent returns partial |
| `PlanValidationError` | once (repair) | supervisor | repair call, then `limits.fallback_plan` |
| `ToolValidationError` | once (model fixes args) | tool source | agent loop |
| `ToolExecutionError` | once | tool source | agent loop, then partial |
| `ToolPermissionError` | no | tool source | agent continues, audit records denial |
| `RunTokenError` | no | tool source (server rejected the token) | run fails with status `auth_error` |
| `RetrievalError` | once | retriever | agent returns partial |
| `CacheError` | no | cache adapter | orchestrator treats the lookup or store as a miss and continues |
| `BudgetExceeded` | no | agent | orchestrator finishes with partial answer |
| `CheckFailed` | once (retry or re-plan) | gate | orchestrator corrective action (6.8) |
| `RunTimeout` | no | orchestrator | run status `timeout` |
| `FixtureMissError` | no | mock provider | eval harness asks for a re-record |

Every error becomes an `ErrorRecord` in the run state and a span event, so the trace shows what went wrong even when the run ends with an answer.

## 16. Configuration and registry

Four YAML files (`config/providers.yaml`, `config/tenants.yaml`, `config/platform.yaml`, `config/prices.yaml`) plus `.env` for secrets. `platform.yaml` selects adapters and limits:

```yaml
orchestrator: langgraph          # v1: agent_loop
tool_sources:
  - name: olist
    adapter: mcp
    url: ${MCP_OLIST_URL}
    manifest: mcp_servers/olist/manifest.yaml   # optional; enables tenant validation
retriever: pgvector
cache: pg_semantic               # none turns every lookup into a miss; tenants opt in
embedder: lmstudio
context_policy: clear_oldest_tool_results
cost_cap_usd: 5.0                # v1: eval run hard ceiling (20.2)
tracing:
  exporter: postgres             # v1: postgres, otlp
limits:
  run_timeout_s: 60
  max_tool_steps: 4
  max_loop_steps: 8              # agent loop orchestrator
  max_plan_size: 3
  fallback_plan: ["docs", "data"]
  model_view_rows: 20
  context_fill: 0.7
  history_turns: 3               # v1
```

A provider entry may also carry `fallback_provider`, `retry_attempts` (default 2) and `retry_backoff_s` (default `[1.0, 3.0]`); the schema requires one backoff value per retry and rejects a self reference, an unknown name, a second fallback level and a missing price row for either side (8.4, 12.4). Environment substitution supports `${VAR:-default}`, including an empty default, which is how a paid provider's key can be absent in a local-only setup.

The registry resolves names through entry points declared in `pyproject.toml`:

```toml
[project.entry-points."switchboard.orchestrators"]
langgraph = "switchboard.adapters.orchestrator.langgraph_impl:LangGraphOrchestrator"
agent_loop = "switchboard.adapters.orchestrator.agent_loop:AgentLoopOrchestrator"

[project.entry-points."switchboard.providers"]
openai_compatible = "switchboard.adapters.providers.openai_compat:OpenAICompatibleProvider"
mock = "switchboard.adapters.providers.mock:MockProvider"

[project.entry-points."switchboard.agents"]
direct = "switchboard.agents.direct:DirectAgent"
docs = "switchboard.agents.docs:DocsAgent"
data = "switchboard.agents.data:DataAgent"
```

A third-party package can add an adapter or an agent by declaring the same entry point group; no change in this repository is needed. Eval suites use the same mechanism.

## 17. Repository layout

```
switchboard/
  README.md  LICENSE  CLAUDE.md  pyproject.toml  uv.lock  docker-compose.yml  .env.example
  config/            providers.yaml  tenants.yaml  platform.yaml  prices.yaml
  docs/
    spec/            requirements.md  design.md  tasks.md  review.md
    adr/             0001-...md
    demo.md          five minute demo script
  src/switchboard/
    config.py        YAML parsing via PyYAML; core/config.py holds the schema
    core/            ports.py  models.py  errors.py  registry.py  config.py  context.py  guardrails.py
    agents/          direct.py  docs.py  data.py  clarify.py (v1)  merge.py  citations.py  results.py  prompts/
    gate/            checks.py (v1)
    adapters/
      orchestrator/  langgraph_impl/  agent_loop/ (v1)
      providers/     openai_compat.py  mock.py  fallback.py (v1)
      embedders/     openai_compat.py  mock.py
      tools/         mcp_client.py  in_process.py  platform_tools.py
      retrievers/    pgvector.py  rewrite.py (v1)  ingest.py
      cache/         none.py  pg_semantic.py (v1)
    tracing/         setup.py  pg_exporter.py  step_events.py  spans.py  cost.py
    api/             app.py  routes/  schemas.py  run_token.py  sse.py
    eval/            runner.py  suites/  scorers/  report.py  baseline.py  harvest.py (v1)
    cli.py
  mcp_servers/olist/ server.py  tools/  manifest.yaml  db.py  auth.py
  web/               package.json  src/  (Vite, React, TypeScript)
  data/
    olist/           README.md (download steps)  schema.sql  load.py
    documents/       synthetic policy markdown files
  evals/
    datasets/v1/     routing.jsonl  toolcall.jsonl  rag.jsonl (v1)  cache.jsonl (v1)  gate.jsonl (v1)  injection.jsonl (v1)
    candidates/      harvested from feedback, unlabeled (v1)
    baselines/       routing.json  toolcall.json
    fixtures/        mock_provider/
    reports/         generated, committed selectively
  tests/
    unit/  contracts/  integration/
  .github/workflows/ci.yml
```

## 18. Testing strategy

| Level | What | Tooling | Runs in CI |
| --- | --- | --- | --- |
| Unit | core models, plan validation, registry, prompt builders (including the stable prefix test), context policy, citation parsing, gate checks, scorers, guardrails | pytest, no I/O | yes |
| Contract | every adapter of a port passes the same test module, parameterized by adapter; providers use recorded fixtures; both orchestrators share one module (v1) | pytest | yes |
| Integration | API plus orchestrator plus MCP server against PostgreSQL, with `MockProvider`; run token forgery and expiry; cross-tenant isolation; trace complete after response | pytest, PostgreSQL service container | yes |
| Eval smoke | `routing` and `toolcall` suites on `mock`, compared to baselines | `switchboard eval run` | yes, gate |
| Eval full | all suites on `lmstudio` and, with explicit flag and cost cap, `deepseek`; both orchestrators in v1 | manual, reports committed | no |
| Live smoke | one real request against LM Studio | manual script | no |

**Mock provider fixtures.** A fixture is keyed by a hash of `(purpose, model, normalized messages, tool specs, response schema)`. Fixtures are recorded from real runs with `--record` and reviewed before commit. A request with no matching fixture fails the test with a message naming the purpose and telling the developer to re-record; it never falls back to a default response.

**What the CI regression gate measures.** Because fixtures replay recorded model output, the CI gate measures the pipeline (parsing, plan validation, tool normalization, scoring) and not the model. A prompt change changes the fixture key, which forces a re-record against a real model and therefore a new report (ADR 0008). The README states this next to the CI badge.

## 19. Deployment

Phase 1 and v1: `docker-compose.yml` with services `postgres` (pgvector image, volume, init SQL for roles and schema), `api` (uvicorn), `mcp-olist`, `web` (static build served by nginx or Vite preview). LM Studio runs on the host and is reached through `host.docker.internal`. Health checks on every service; `api` waits for `postgres` and `mcp-olist`. The SSE endpoint requires proxy buffering to be disabled in the `web` service's nginx config.

v2 (optional): Azure Container Apps for `api`, `mcp-olist` and `web`, Azure Database for PostgreSQL flexible server with pgvector, Key Vault for secrets, Terraform in `infra/`, GitHub Actions workflow that builds images on tag and applies Terraform with a manual approval environment.

## 20. Evaluation design

### 20.1 Datasets

JSONL, versioned by directory (`evals/datasets/v1`). A dataset version never changes after a report references it; fixes go into v2.

| Suite | Case fields | Size (Phase 1 or v1) | Scorer |
| --- | --- | --- | --- |
| routing | `query`, `expected_agents`, `tags`, `split` | 60, balanced across plans; `ambiguous` where blind labelers disagree | exact set match (plan accuracy); Jaccard as partial credit; confusion matrix over plan labels |
| toolcall | `query`, `expected_tool`, `expected_args`, `tool_version` | 40 | tool name match and argument AST match (required args exact, optional args if present); trajectory: tool calls per case and redundant calls |
| rag (v1) | `query`, `relevant_chunk_ids` (portable `source#ordinal` keys), `needs_rewrite` tag, optional `history`, `split` | 40 | recall@5, MRR for four retrieval strategies (9.3); rubric scoring by judge in v2 |
| cache (v1) | `query_a`, `query_b`, `should_hit` | 50 pairs, including cross-type near misses | hit rate, false-hit rate per threshold |
| gate (v1) | `query`, `answer`, `agents`, `plan`, `result_sources`, `evidence`, `is_weak`, `weakness` | 30 | precision, recall and F1 per check; overall weak precision, recall and good-case specificity |
| injection (v1) | `content`, `is_malicious` | 40 | detection rate, false positive rate |

Construction (Phase 1): routing questions are written by a separate session from a brief and labeled blind by the author; toolcall cases are generated from templates with arguments computed by code. Every case has a deterministic `dev` or `test` split; prompt work may read only dev cases, and reports score the test split. Details in `evals/datasets/v1/README.md`.

Dataset versions are per suite when a suite is added later: `v2/gate.jsonl` holds the 30 gate cases and leaves `v1/` untouched for routing and toolcall. The gate labels are constructed by the implementation session and audited by reading, not blind human labels; the suite README and the report state this. One case is labeled acceptable although `numbers_grounded` rejects it, so the known derived-number false positive is measured. Details in `evals/datasets/v2/README.md`.

`v3/rag.jsonl` holds the 40 rag cases and leaves `v1/` and `v2/` untouched. Chunk ids are database-generated and change on a fresh ingest, so the dataset stores portable `source#ordinal` keys and the suite resolves them to current chunk ids at eval time; the README documents the format and the failure mode for a key that no longer exists. Nine cases carry the `needs_rewrite` tag and four carry `history`; all 40 are in the `test` split because the task asks for the four strategies on the same 40 cases and the rewrite prompt was not fitted to individual cases. Details in `evals/datasets/v3/README.md`.

`v4/injection.jsonl` holds the 40 injection samples and leaves `v1/`, `v2/` and `v3/` untouched: 20 malicious samples, each labeled with the pattern id it was written for, and 20 benign near-misses, including two deliberate false-positive traps. The suite runs the deterministic scanner in `core/guardrails.py` and reports detection rate, false positive rate and per-pattern precision and recall; labels are constructed and audited, not blind human labels. All 40 are in the `test` split because there is no prompt to tune. Details in `evals/datasets/v4/README.md`.

`v5/cache.jsonl` holds the 50 cache pairs and leaves `v1/` to `v4/` untouched: 25 paraphrases labeled `should_hit` and 25 near misses, six of them cross-type (a policy question next to a data question). The suite embeds both questions once with bge-m3, reports the cosine similarity and sweeps eight thresholds from 0.85 to 0.99. The labels are constructed and audited by the implementation session, not blind human labels; all 50 are in the `test` split because the threshold is chosen from the whole set rather than tuned on a dev split. Details in `evals/datasets/v5/README.md`.

Feedback (v1): `POST /runs/{id}/feedback` stores a rating of -1 or 1 and an optional note of at most 2000 characters. It is append-only, so a changed rating is a new row and the newest one is the run's verdict. `switchboard eval harvest --since <date> [--tenant <id>]` exports the runs whose newest feedback is negative and whose feedback was written on or after that UTC date, one unlabeled case per run, to `evals/candidates/feedback-<since>-<tenant|all>.jsonl`. A candidate carries `candidate_id` (`candidate-<run_id>`), `run_id`, `tenant_id`, `query`, `answer`, `plan`, `status`, `rating`, `note`, `run_started_at`, `run_ended_at` and `rated_at`, and no expected label of any kind. The command rewrites the file from the current rows on every run, so running it twice writes the same bytes instead of appending a duplicate, and it never modifies a frozen dataset or a baseline. A human labels the candidates; labeled cases enter the next dataset version, never the current one.

### 20.2 Runner

`switchboard eval run --suite <name> --provider <name> [--orchestrator <name>] [--limit n] [--record]` executes cases concurrently (bounded, default 4), applies a per-case timeout, times each case, collects usage including cached input tokens, scores, and writes `evals/reports/<suite>-<provider>.md` and `.json`. A second orchestrator gets its own file, `<suite>-<orchestrator>-<provider>`, so the committed LangGraph reports keep their names. The JSON report carries the dataset version, split, orchestrator, provider, model, metrics, p50 and p95 latency, input, cached input and output tokens and one row per case. It also carries `cost_usd`, computed from the run's summed usage and the provider's price row, plus the row itself (`price`: rates, `verified_on`, `source`) and `no_api_cost` for a zero-price local provider; `cost_usd` and `price` are null when the provider has no row, and a mock replay has no row on purpose. A report is never backfilled: a report written before the price table keeps its null. A run with `--limit` is marked as a smoke run in both files. `--provider mock --record` records missing fixtures through the real provider named by the default tenant; a plain mock run replays them with no network. `--provider deepseek` prints the estimated cost from the price table and the case count and refuses to proceed without `--confirm-cost` or above the cap in `platform.yaml`. The estimate is deliberately conservative: 4,000 input and 1,000 output tokens per model call, no cached tokens (cached input is cheaper), one call per routing and toolcall case and six per rag case (four strategies plus up to two rewrites). It is a planning figure, not a measurement: the report records the provider's usage. The cap is `platform.cost_cap_usd` (default 5.0 USD) and `--cost-cap-usd` overrides it for one run; a refusal exits non-zero before the provider is built, so it needs no key and makes no network call. A provider with an explicit zero row (LM Studio) prints 0 and needs no confirmation; a mock replay is exempt; a non-mock provider with no price row is refused, because its run cannot be estimated.

Suites receive `EvalDeps(provider)`. The routing suite builds the production supervisor request from the shared `switchboard.routing` helpers and the toolcall suite sends the five manifest tool specs with the data agent prompt prefix; both make one completion per case and do not run the agents or execute tools. This keeps 60 routing and 40 toolcall cases within the local-model budget and lets the mock provider replay them in CI. The gate suite makes no model call: it runs the same deterministic checks as the orchestrator and reports the model as `deterministic`, so its result depends only on the dataset and the code.

With `--orchestrator agent_loop` the routing suite sends the first step of the agent loop instead: the shared loop prompt from `switchboard.agent_loop`, the default tenant's read-only tools plus `result_page@1` and `search_docs@1` (only when a retriever is configured), one completion per case, no tool execution, and the plan derived from that decision with the same `derive_plan` function the adapter uses. The report records `plan_source` and says the number measures the first decision: a live loop can call more tools on later steps, so exact-set accuracy can differ in either direction. The fixtures are recorded with `--record` from the same request, so the mock replay in `evals/reports/routing-agent_loop-mock.*` is reproducible. With `--orchestrator agent_loop` the toolcall suite sends the same first loop step and scores its first tool call against the dataset label; the report states that the loop is not continued, so a live loop can choose a different or additional tool later. Both modes are one completion per case and execute no tool, and the routing and toolcall suites share one helper for the loop tool list so the two measurements cannot drift.

The rag suite opens the tenant's document index, so it needs `SWITCHBOARD_DATABASE_URL` and the configured embedder, not just a provider. It runs the four strategies from 9.3 on every case and makes rewrite calls through `EvalDeps.provider`; `--provider mock --record` records those calls through the real provider and a later mock run replays them, but retrieval still uses the real embedding model, so the suite is not part of the CI mock gate. The JSON report carries a `strategies` object with recall@5, MRR, rewrite and retrieval round counts and latency per strategy, and the markdown shows the same table plus one row per case.

The injection suite is deterministic like the gate suite: it runs `core/guardrails.py` on each case and makes no model call, so the report's model field is `deterministic` and the result depends only on the dataset and the pattern set.

The cache suite also makes no model call: it embeds both questions of every pair once with the configured embedder and reports the cosine similarity. It needs LM Studio or `--provider mock`; the mock run uses the deterministic hash embedder, which has no semantic similarity, so the report labels it a mechanics check and never treats it as a quality measurement. The JSON report carries `threshold_sweep` (hits, false hits, hit rate, false-hit rate and accuracy per threshold) and the markdown shows the same table with the tenant's configured threshold marked. This is the suite that chooses the threshold in section 10.2. It is not part of the CI mock gate.

The rag suite accepts `--orchestrator` only as the comparison dimension: both adapters share the retriever and the rewrite helper, so the four strategies and their code path do not depend on the orchestrator. The report records the label and says that any difference between the two runs is run-to-run timing, not a mechanism difference. The deterministic gate, injection and cache suites have no orchestrator dimension and refuse the flag with a named error.

### 20.3 Comparison and regression

`switchboard eval compare` reads the per-suite JSON reports and writes `evals/reports/compare.md` and `.json` (name configurable): one row per suite, provider and orchestrator for routing, toolcall and rag, with the report's dataset version, case count, headline and secondary metric, p50 and p95 latency, tokens per 100 queries and cost per 100 queries. The headline is `plan_accuracy`, `both_accuracy`, or `recall_at_5_conditional_hybrid` (the production retrieval path); the secondary metric is `plan_jaccard`, `argument_accuracy` or the matching MRR. Tokens and cost per 100 queries are the report's measured totals scaled by its case count. A combination with no report is marked `not run`, and `--not-run-reason provider=reason` records why, so a provider that is configured but not yet exercised is visible in the table. A smoke report (`--limit`) is never compared. The output has no timestamp and a fixed row order, so the same reports write the same bytes, and the command only reads: it never rewrites a per-suite report or a baseline. `switchboard eval gate --suite routing --baseline evals/baselines/routing.json --max-drop 0.05` exits non-zero when accuracy drops more than the delta; CI runs this on the mock provider (see section 18 for what that measures). Baselines are updated deliberately by committing a new file, never automatically.

## 21. Technology stack and dependency allowlist

This list is the allowlist referred to in the working agreements: a dependency not named here needs a reason in its commit message and a line added here. Exact versions are **not** written in this document. T1.01 looks up the current stable release of each package on the package index on the day it runs, pins it in `pyproject.toml` or `package.json`, and the lock files are the record. Versions are never filled in from memory.

| Area | Choice | Notes |
| --- | --- | --- |
| Python | 3.12, managed by uv | |
| Build backend | uv_build | the backend maintained with uv, so there is no second build toolchain to pin |
| Data models | pydantic v2 | the only non-stdlib import allowed in `core` |
| API | FastAPI, uvicorn | SSE through FastAPI's `StreamingResponse`, no extra package |
| Orchestration | langgraph | imported only in `adapters/orchestrator/langgraph_impl` |
| Model clients | openai (official Python SDK) | for every OpenAI-compatible provider and embedder |
| MCP | mcp (official Python SDK), using `mcp.server.mcpserver.MCPServer` for the server and its streamable HTTP client for `McpToolSource` | SDK 2.x renamed FastMCP to MCPServer; not the separate `fastmcp` package, one SDK on both sides |
| Database | asyncpg; PostgreSQL 16 with pgvector via the `pgvector/pgvector` image (pg16 tag) | no ORM |
| Observability | opentelemetry-api, opentelemetry-sdk; v1 adds the official OTLP/HTTP exporter (opentelemetry-exporter-otlp-proto-http) | the OTLP exporter is used only when `tracing.exporter: otlp`; PostgreSQL stays the default |
| Tokens | PyJWT | ADR 0014 |
| Config | PyYAML | env substitution and the schema are hand-written in core/config.py; YAML parsing lives in switchboard/config.py so core stays standard library plus pydantic |
| CLI | typer | |
| SQL validation (v2) | sqlglot | |
| Re-ranking (v1, optional) | sentence-transformers cross-encoder | only if T2.03 enables re-ranking; T2.03 does not, so it stays an optional row with no new dependency |
| Dev tools | ruff, mypy, pytest, pytest-asyncio, import-linter, pre-commit, httpx (the FastAPI test client needs it for HTTP and SSE tests) | |
| Web | Node LTS, Vite, React, TypeScript, @vitejs/plugin-react | the plugin builds React with Vite; @types/react and @types/react-dom are the type declarations. No UI framework in Phase 1 |
| Local models | LM Studio on the host (8.1) | |
