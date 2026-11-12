"""Domain models shared by every adapter.

These are the contract described in design.md section 4. They are plain
pydantic models: they carry no behaviour beyond small, pure helpers, so core
stays free of framework imports. A later task that changes a field updates
design.md section 4 in the same commit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RunStatus = Literal[
    "running",
    "ok",
    "partial",
    "needs_clarification",
    "timeout",
    "provider_error",
    "auth_error",
    "waiting_approval",
]


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    result_id: str | None = None
    total_rows: int | None = None
    cleared: bool = False


class ProviderCapabilities(BaseModel):
    tools: bool
    json_schema: bool
    streaming: bool
    reasoning: bool
    context_window: int = Field(gt=0)


class Usage(BaseModel):
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def plus(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class ToolSpec(BaseModel):
    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    risk: Literal["read", "write"] = "read"
    tenant_scoped: bool = False
    sensitive_args: list[str] = Field(default_factory=list)

    @property
    def wire_name(self) -> str:
        return f"{self.name}@{self.version}"


class CompletionRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompletionResponse(BaseModel):
    text: str | None
    reasoning: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    parsed: dict[str, Any] | None = None
    structured_output_strategy: (
        Literal["native_json_schema", "tool_call", "prompted_json"] | None
    ) = None
    usage: Usage
    model: str
    provider: str
    latency_ms: int = Field(ge=0)


class ToolResultView(BaseModel):
    result_id: str
    tool: str
    total_rows: int = Field(ge=0)
    summary: dict[str, Any] | None = None
    rows_preview: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool


class ToolResult(BaseModel):
    tool: str
    version: str
    total_rows: int = Field(ge=0)
    summary: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    result_id: str | None = None
    audit_id: str | None = None
    duration_ms: int = Field(default=0, ge=0)

    def view(self, *, limit: int, result_id: str | None = None) -> ToolResultView:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        identifier = result_id or self.result_id
        if not identifier:
            raise ValueError("a result id is required to build a view")
        return ToolResultView(
            result_id=identifier,
            tool=self.tool,
            total_rows=self.total_rows,
            summary=self.summary,
            rows_preview=self.rows[:limit],
            truncated=self.truncated_for(limit),
        )

    def truncated_for(self, limit: int) -> bool:
        return len(self.rows) > limit or self.total_rows > limit


class Citation(BaseModel):
    kind: Literal["document", "tool"]
    ref: str
    source_id: str
    span_id: str | None = None
    audit_id: str | None = None
    snippet: str


class EvidenceRef(BaseModel):
    kind: Literal["document", "tool"]
    ref: str
    source_id: str
    # The content the agent was shown, so the deterministic gate can check
    # numbers against it (design.md sections 6.8 and 4).
    text: str = ""
    empty: bool = False


class AgentSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(min_length=1)
    cacheable: bool
    exclusive: bool = False
    needs: list[Literal["tools", "retriever"]] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def description_is_one_line(cls, value: str) -> str:
        if "\n" in value:
            raise ValueError("description must be one line")
        return value


class RoutingDecision(BaseModel):
    agents: list[str]
    reason: str

    @property
    def label(self) -> str:
        return "+".join(sorted(self.agents))


class Budget(BaseModel):
    max_tool_steps: int = Field(default=4, ge=0)
    max_tokens: int = Field(default=4096, ge=1)


class AgentTask(BaseModel):
    query: str
    history: list[Turn] = Field(default_factory=list)
    feedback: str | None = None
    budget: Budget = Field(default_factory=Budget)


class RetrievalSettings(BaseModel):
    k: int = Field(default=5, ge=1)
    min_score: float = Field(default=0.35, ge=0.0, le=1.0)
    # ``rewrite`` enables the model call of design.md section 9.3; the mode
    # picks between the conditional trigger (history or a low top score) and
    # rewriting every query (used by the rag eval, never by the docs agent).
    rewrite: bool = False
    rewrite_mode: Literal["conditional", "always"] = "conditional"
    # Phase 1 behaviour stays selectable with ``vector``; tenants that opt in
    # get full-text search plus reciprocal rank fusion (design.md section 9.2).
    search: Literal["vector", "hybrid"] = "vector"


class CacheSettings(BaseModel):
    enabled: bool = False
    threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    ttl_days: int = Field(default=7, ge=1)


class TenantContext(BaseModel):
    tenant_id: str
    allowed_agents: set[str]
    allowed_tools: set[str]
    ask_tools: set[str] = Field(default_factory=set)
    seller_ids: list[str] | None = None
    provider: str
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)


class ToolCallRecord(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["ok", "error", "denied"]
    result_id: str | None = None
    audit_id: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    error: str | None = None


class AgentResult(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    usage: Usage
    partial: bool = False


class RunContext(BaseModel):
    run_id: str
    tenant: TenantContext
    run_token: str = Field(repr=False)
    trace_parent: str


class CacheKey(BaseModel):
    tenant_id: str
    normalized_query: str
    embedding: list[float]
    index_version: int


class CachedAnswer(BaseModel):
    plan: str
    answer: AgentResult


class CacheHit(BaseModel):
    entry: CachedAnswer
    similarity: float = Field(ge=0.0, le=1.0)


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    source: str
    title: str
    text: str
    score: float
    ordinal: int = Field(ge=0)


class ChatRequest(BaseModel):
    query: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str | None = None
    history: list[Turn] = Field(default_factory=list)


class ErrorRecord(BaseModel):
    kind: str
    message: str
    retryable: bool = False
    stage: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class GateVerdict(BaseModel):
    passed: bool
    failed_checks: list[str] = Field(default_factory=list)
    action: Literal["pass", "retry", "replan", "none"] = "pass"
    reason: str = ""
    feedback: str | None = None


class RunResult(BaseModel):
    run_id: str
    tenant_id: str
    query: str
    plan: RoutingDecision | None = None
    previous_plan: RoutingDecision | None = None
    answer: AgentResult | None = None
    status: RunStatus = "running"
    usage: Usage = Field(default_factory=lambda: Usage(input_tokens=0, output_tokens=0))
    cache_hit: bool = False
    attempts: int = Field(default=0, ge=0)
    gate: GateVerdict | None = None
    errors: list[ErrorRecord] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ContextBudget(BaseModel):
    context_window: int = Field(gt=0)
    max_tokens: int = Field(ge=0)
    fill_ratio: float = Field(default=0.7, gt=0.0, le=1.0)

    @property
    def limit_tokens(self) -> int:
        usable = max(0, self.context_window - self.max_tokens)
        return int(usable * self.fill_ratio)


class FitResult(BaseModel):
    messages: list[Message]
    estimated_tokens: int = Field(ge=0)
    limit_tokens: int = Field(ge=0)
    compacted: bool
    cleared_results: int = Field(ge=0)


class EvalCase(BaseModel):
    id: str
    query: str
    expected_agents: list[str] = Field(default_factory=list)
    expected_tool: str | None = None
    expected_args: dict[str, Any] = Field(default_factory=dict)
    tool_version: str | None = None
    tags: list[str] = Field(default_factory=list)
    split: Literal["dev", "test"] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EvalOutcome(BaseModel):
    case_id: str
    answer: str | None = None
    plan: list[str] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    tool_arguments: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    usage: Usage | None = None
    latency_ms: int = Field(default=0, ge=0)
    error: str | None = None


class Score(BaseModel):
    case_id: str
    metrics: dict[str, float] = Field(default_factory=dict)
    passed: bool | None = None
