// Typed client for the Switchboard API. The browser always calls a relative
// /api base: nginx proxies it in the compose stack (web/nginx.conf) and the Vite
// dev server does the same in vite.config.ts.

const API_BASE = "/api";

export type RunStatus =
  | "running"
  | "ok"
  | "partial"
  | "needs_clarification"
  | "timeout"
  | "provider_error"
  | "auth_error"
  | "waiting_approval";

export interface Citation {
  kind: "document" | "tool";
  ref: string;
  source_id: string;
  span_id: string | null;
  audit_id: string | null;
  snippet: string;
}

export interface Usage {
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
}

export interface ErrorRecord {
  kind: string;
  message: string;
  retryable: boolean;
  stage: string | null;
  details: Record<string, unknown>;
}

export interface RoutingDecision {
  agents: string[];
  reason: string;
}

export interface ChatResponse {
  run_id: string;
  tenant_id: string;
  session_id: string | null;
  status: RunStatus;
  answer: string | null;
  plan: RoutingDecision | null;
  citations: Citation[];
  usage: Usage;
  cost_usd: number | null;
  cache_hit: boolean;
  attempts: number;
  errors: ErrorRecord[];
  started_at: string | null;
  ended_at: string | null;
  trace_incomplete: boolean;
}

export interface RunSummary {
  run_id: string;
  tenant_id: string;
  session_id: string | null;
  query: string;
  orchestrator: string;
  provider: string | null;
  plan: string | null;
  status: RunStatus;
  cache_hit: boolean;
  attempts: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  trace_complete: boolean;
  started_at: string | null;
  ended_at: string | null;
  answer: string | null;
  citations: Citation[];
  errors: ErrorRecord[];
  // Only GET /runs/{id} loads it; the run list leaves it null.
  latest_feedback: FeedbackSummary | null;
}

// Feedback is append-only: -1 negative, 1 positive, and the newest row is the
// run's current verdict (POST /runs/{id}/feedback).
export interface FeedbackSummary {
  id: string;
  run_id: string;
  tenant_id: string;
  rating: -1 | 1;
  note: string | null;
  created_at: string;
}

export interface RunsResponse {
  runs: RunSummary[];
  count: number;
}

export interface SessionSummary {
  session_id: string;
  tenant_id: string;
  title: string;
  last_query: string;
  status: RunStatus;
  plan: string | null;
  run_count: number;
  started_at: string;
  last_run_at: string;
}

export interface SessionsResponse {
  sessions: SessionSummary[];
  count: number;
}

export interface SpanEvent {
  name: string;
  time: string;
  attributes: Record<string, unknown>;
}

export interface TraceSpan {
  span_id: string;
  parent_span_id: string | null;
  trace_id: string;
  name: string;
  kind: string;
  status: string;
  start: string;
  end: string;
  duration_ms: number;
  attributes: Record<string, unknown>;
  events: SpanEvent[];
  children: TraceSpan[];
}

export interface TraceResponse {
  run_id: string;
  trace_id: string | null;
  incomplete: boolean;
  span_count: number;
  spans: TraceSpan[];
}

// One server-sent event. The API merges the event payload with its run id, so
// the fields of run.started, plan.decided and the rest sit at the top level.
export interface StepEvent {
  type: string;
  run_id: string;
  [key: string]: unknown;
}

// GET /settings returns the read-only runtime snapshot built at startup. Every
// value is the configuration the API actually loaded; the endpoint has no
// write counterpart and carries no key, base URL or database URL.
export interface SettingsCapabilities {
  tools: boolean;
  json_schema: boolean;
  streaming: boolean;
  reasoning: boolean;
  context_window: number;
}

export interface SettingsPrice {
  input_per_million: number;
  cached_input_per_million: number;
  output_per_million: number;
  no_api_cost: boolean;
  verified_on: string | null;
}

export interface SettingsLimits {
  run_timeout_s: number;
  max_tool_steps: number;
  max_loop_steps: number;
  max_plan_size: number;
  fallback_plan: string[];
  model_view_rows: number;
  context_fill: number;
  history_turns: number;
}

export interface SettingsPlatform {
  orchestrator: string;
  retriever: string;
  embedder: string;
  cache: string;
  context_policy: string;
  limits: SettingsLimits;
}

export interface SettingsRetrieval {
  k: number;
  min_score: number;
  rewrite: boolean;
  rewrite_mode: "conditional" | "always";
  search: "vector" | "hybrid";
}

export interface SettingsCache {
  enabled: boolean;
  threshold: number;
  ttl_days: number;
}

export interface SettingsTenant {
  tenant_id: string;
  provider: string;
  seller_ids: string[] | null;
  agents_allow: string[];
  tools_allow: string[];
  tools_ask: string[];
  retrieval: SettingsRetrieval;
  cache: SettingsCache;
}

export interface SettingsProvider {
  name: string;
  model: string | null;
  capabilities: SettingsCapabilities | null;
  price: SettingsPrice | null;
}

export interface SettingsTool {
  name: string;
  version: string;
  wire_name: string;
  description: string;
  risk: "read" | "write";
  tenant_scoped: boolean;
  allowed: boolean;
}

export interface SettingsAgent {
  name: string;
  description: string;
  cacheable: boolean;
  needs: string[];
  allowed: boolean;
}

export interface SettingsResponse {
  read_only: boolean;
  platform: SettingsPlatform;
  tenant: SettingsTenant;
  provider: SettingsProvider;
  tools: SettingsTool[];
  agents: SettingsAgent[];
  tenants: string[];
}

export type HealthState = "ok" | "error" | "unknown";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function describeHttpError(response: Response): Promise<string> {
  const text = await response.text();
  if (text === "") {
    return `HTTP ${response.status}`;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (isRecord(parsed) && "detail" in parsed) {
      const detail = parsed.detail;
      const rendered = typeof detail === "string" ? detail : JSON.stringify(detail);
      return `HTTP ${response.status}: ${rendered}`;
    }
  } catch {
    // Not JSON: fall through to the raw response text.
  }
  return `HTTP ${response.status}: ${text}`;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(await describeHttpError(response), response.status);
  }
  return (await response.json()) as T;
}

export async function postJson<T>(path: string, payload: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new ApiError(await describeHttpError(response), response.status);
  }
  return (await response.json()) as T;
}

function parseStepEvent(raw: string): StepEvent | null {
  let type = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      type = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  const data = dataLines.join("\n");
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    throw new Error(`${type} carried data that is not JSON: ${data}`);
  }
  if (!isRecord(parsed) || typeof parsed.run_id !== "string") {
    throw new Error(`${type} carried a payload without a run id: ${data}`);
  }
  return { ...parsed, type } as StepEvent;
}

/**
 * POST /chat/stream and dispatch each SSE event as it arrives. The stream ends
 * with run.finished; a closed stream that never delivered it is reported by the
 * caller, never replaced by a made-up result.
 */
export async function postChatStream(
  payload: { query: string; tenant_id: string; session_id?: string | null },
  onEvent: (event: StepEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    throw new ApiError(await describeHttpError(response), response.status);
  }
  if (response.body === null) {
    throw new Error("the API returned an event stream without a body");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const consume = (block: string) => {
    const event = parseStepEvent(block);
    if (event !== null) {
      onEvent(event);
    }
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        consume(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
    }
    buffer += decoder.decode();
    if (buffer.trim() !== "") {
      consume(buffer);
    }
  } finally {
    reader.releaseLock();
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/**
 * GET /health, which answers 200 only when every dependency is ok and 503 with
 * the same body shape otherwise. The sidebar status dot reads this, so an
 * unhealthy stack says so instead of showing a decorative green dot.
 */
export async function getHealthState(signal?: AbortSignal): Promise<HealthState> {
  try {
    const response = await fetch(`${API_BASE}/health`, {
      signal,
      headers: { Accept: "application/json" },
    });
    const payload: unknown = await response.json();
    if (!isRecord(payload)) {
      return "error";
    }
    return payload.status === "ok" ? "ok" : "error";
  } catch (caught) {
    if (isAbortError(caught)) {
      throw caught;
    }
    return "error";
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export function toChatResponse(event: StepEvent): ChatResponse {
  // run.finished carries the same payload as POST /chat (plus the run id).
  return event as unknown as ChatResponse;
}

export function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
