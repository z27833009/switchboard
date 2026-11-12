import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import {
  asNumber,
  asString,
  asStringArray,
  errorMessage,
  isAbortError,
  postChatStream,
  toChatResponse,
} from "../api";
import type { ChatResponse, StepEvent } from "../api";
import { CitationChips, ErrorList, StatusBadge } from "../components/Badges";
import { FeedbackForm } from "../components/Feedback";
import { TENANTS } from "../config";
import { formatDateTime, formatDuration, formatTokens } from "../format";

function stepSummary(step: StepEvent): string {
  switch (step.type) {
    case "run.started":
      return `run ${step.run_id} started`;
    case "plan.decided": {
      const plan = asString(step.plan);
      const agents = asStringArray(step.agents);
      const reason = asString(step.reason);
      const planText = plan === "" ? "no plan label" : `plan ${plan}`;
      const agentText = agents.length === 0 ? "" : ` (${agents.join(", ")})`;
      return `${planText}${agentText}${reason === "" ? "" : `: ${reason}`}`;
    }
    case "agent.started":
      return `agent ${asString(step.agent) || "unknown"} started`;
    case "agent.finished": {
      const partial = step.partial === true;
      const citations = asNumber(step.citations);
      return `agent ${asString(step.agent) || "unknown"} finished${partial ? " (partial)" : ""}${
        citations === null ? "" : `, ${citations} citation(s)`
      }`;
    }
    case "tool.called": {
      const rows = asNumber(step.total_rows);
      const duration = asNumber(step.duration_ms);
      return `${asString(step.tool) || "tool"}: ${asString(step.status) || "unknown"}${
        rows === null ? "" : `, ${rows} row(s)`
      }${duration === null ? "" : `, ${formatDuration(duration)}`}`;
    }
    case "gate.checked":
      return `${asString(step.verdict) || "unknown"} (${asString(step.action) || "unknown"})`;
    case "answer.delta":
      return `${asString(step.text).length} character(s) of answer`;
    case "run.finished":
      return `status ${asString(step.status) || "unknown"}`;
    default:
      return JSON.stringify(step);
  }
}

function StepTimeline({ steps }: { steps: StepEvent[] }) {
  return (
    <section className="panel">
      <h3>Live steps</h3>
      <ol className="steps">
        {steps.map((step, index) => (
          <li key={`${step.type}-${index}`} className="step">
            <div className="step-line">
              <span className="step-type">{step.type}</span>
              <span>{stepSummary(step)}</span>
            </div>
            <details>
              <summary>payload</summary>
              <pre>{JSON.stringify(step, null, 2)}</pre>
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}

function AnswerPanel({ result }: { result: ChatResponse }) {
  const plan = result.plan;
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Answer</h3>
        <StatusBadge status={result.status} />
        {result.trace_incomplete ? <span className="badge badge-warning">trace incomplete</span> : null}
      </div>
      <p className="meta">
        run <code>{result.run_id}</code> tenant <code>{result.tenant_id}</code>
        {result.session_id !== null ? (
          <>
            {" "}
            session <code>{result.session_id}</code>
          </>
        ) : null}
        {plan !== null ? ` plan ${plan.agents.join("+") || "(none)"}` : " no plan"}
        {result.cache_hit ? " cache hit" : ""}
      </p>
      {result.answer !== null && result.answer !== "" ? (
        <p className="answer-text">{result.answer}</p>
      ) : (
        <p className="muted">The API returned no answer for this status.</p>
      )}
      <CitationChips
        runId={result.run_id}
        tenantId={result.tenant_id}
        citations={result.citations}
      />
      <ErrorList errors={result.errors} />
      <dl className="facts">
        <div>
          <dt>input tokens</dt>
          <dd>{formatTokens(result.usage.input_tokens)}</dd>
        </div>
        <div>
          <dt>output tokens</dt>
          <dd>{formatTokens(result.usage.output_tokens)}</dd>
        </div>
        <div>
          <dt>attempts</dt>
          <dd>{result.attempts}</dd>
        </div>
        <div>
          <dt>started</dt>
          <dd>{formatDateTime(result.started_at)}</dd>
        </div>
        <div>
          <dt>ended</dt>
          <dd>{formatDateTime(result.ended_at)}</dd>
        </div>
      </dl>
      {result.trace_incomplete ? (
        <p className="warning-text">
          The API reported that the trace is incomplete because the span flush did not finish. The trace page
          will show only the spans that were stored.
        </p>
      ) : null}
      <p className="links">
        <a href={`#/runs/${encodeURIComponent(result.run_id)}/trace`}>Open trace</a>
        <a href={`#/runs/${encodeURIComponent(result.run_id)}`}>Open run</a>
      </p>
      <FeedbackForm
        key={result.run_id}
        runId={result.run_id}
        tenantId={result.tenant_id}
        latest={null}
      />
    </section>
  );
}

function ClarifyPanel({ result }: { result: ChatResponse }) {
  // needs_clarification is not an answer: the agent asked a question back, so
  // the panel shows the question and the status, never answer styling.
  return (
    <section className="panel panel-clarify">
      <div className="panel-header">
        <h3>Clarification needed</h3>
        <StatusBadge status={result.status} />
      </div>
      <p className="meta">
        run <code>{result.run_id}</code> tenant <code>{result.tenant_id}</code>
        {result.session_id !== null ? (
          <>
            {" "}
            session <code>{result.session_id}</code>
          </>
        ) : null}
        {result.plan !== null ? ` plan ${result.plan.agents.join("+") || "(none)"}` : " no plan"}
      </p>
      <p className="clarify-question">
        {result.answer !== null && result.answer !== ""
          ? result.answer
          : "The agent asked for clarification without a question."}
      </p>
      <p className="muted">
        Answer in the same conversation and the stored turn is used as context for the next request. This
        run ended as <code>needs_clarification</code>; it is not a factual answer and has no citations.
      </p>
      <ErrorList errors={result.errors} />
      <p className="links">
        <a href={`#/runs/${encodeURIComponent(result.run_id)}`}>Open run</a>
      </p>
    </section>
  );
}

function newSessionId(): string {
  // One id per conversation, generated here so the server-side turns of this
  // browser session are the ones the next request receives (T2.09).
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function StreamingAnswerPanel({ text }: { text: string }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Answer</h3>
        <span className="badge badge-running">streaming</span>
      </div>
      <p className="answer-text">{text}</p>
    </section>
  );
}

export function ChatPage() {
  const [tenant, setTenant] = useState(TENANTS[0]);
  const [query, setQuery] = useState("");
  const [sessionId, setSessionId] = useState<string>(() => newSessionId());
  const [steps, setSteps] = useState<StepEvent[]>([]);
  const [streamedAnswer, setStreamedAnswer] = useState("");
  const [result, setResult] = useState<ChatResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [endedWithoutResult, setEndedWithoutResult] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      controllerRef.current?.abort();
    };
  }, []);

  function startNewConversation() {
    if (streaming) {
      return;
    }
    setSessionId(newSessionId());
    setSteps([]);
    setStreamedAnswer("");
    setResult(null);
    setError(null);
    setEndedWithoutResult(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (trimmed === "" || streaming) {
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setStreaming(true);
    setSteps([]);
    setStreamedAnswer("");
    setResult(null);
    setError(null);
    setEndedWithoutResult(false);
    let sawRunFinished = false;
    try {
      await postChatStream(
        { query: trimmed, tenant_id: tenant, session_id: sessionId },
        (step) => {
          if (step.type === "answer.delta") {
            // Deltas go straight to the live answer. The step timeline keeps
            // the run's steps, not every token fragment.
            setStreamedAnswer((current) => current + asString(step.text));
            return;
          }
          setSteps((current) => [...current, step]);
          if (step.type === "run.finished") {
            sawRunFinished = true;
            setResult(toChatResponse(step));
          }
        },
        controller.signal,
      );
      if (!sawRunFinished && !controller.signal.aborted) {
        setEndedWithoutResult(true);
      }
    } catch (caught) {
      if (!isAbortError(caught)) {
        setError(errorMessage(caught));
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
      }
      setStreaming(false);
    }
  }

  return (
    <div className="page">
      <section className="panel">
        <h2>Chat</h2>
        <form className="chat-form" onSubmit={handleSubmit}>
          <label className="field">
            <span>Tenant</span>
            <select
              value={tenant}
              onChange={(event) => setTenant(event.target.value)}
              disabled={streaming}
            >
              {TENANTS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="field field-grow">
            <span>Query</span>
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              rows={3}
              placeholder="Ask about orders, products or policies"
              disabled={streaming}
            />
          </label>
          <button type="submit" disabled={streaming || query.trim() === ""}>
            {streaming ? "Running..." : "Send"}
          </button>
        </form>
        <p className="meta session-line">
          session <code>{sessionId}</code>{" "}
          <button type="button" className="link-button" onClick={startNewConversation} disabled={streaming}>
            New conversation
          </button>
        </p>
        {streaming ? <p className="muted">Waiting for steps. Each step appears when the API publishes it.</p> : null}
      </section>

      {error !== null ? (
        <div className="error-box">
          <strong>Request failed</strong>
          <p>{error}</p>
        </div>
      ) : null}
      {endedWithoutResult ? (
        <div className="error-box">
          <strong>The stream ended without run.finished</strong>
          <p>
            The API closed the event stream before the final payload. The run may still be running; check
            the <a href="#/runs">run list</a> for its stored result.
          </p>
        </div>
      ) : null}

      {steps.length > 0 ? <StepTimeline steps={steps} /> : null}
      {result === null && streamedAnswer !== "" ? (
        <StreamingAnswerPanel text={streamedAnswer} />
      ) : null}
      {result !== null && result.status === "needs_clarification" ? (
        <ClarifyPanel result={result} />
      ) : null}
      {result !== null && result.status !== "needs_clarification" ? (
        <AnswerPanel result={result} />
      ) : null}

      {steps.length === 0 && !streaming && error === null && !endedWithoutResult ? (
        <section className="panel">
          <p className="muted">
            Send a question to stream the run. Answer text appears as it is generated and is replaced
            by the final answer, with its citations, after run.finished. Follow-ups such as "And in
            April?" reuse this conversation through the session id shown above.
          </p>
        </section>
      ) : null}

      <details className="panel raw-details">
        <summary>How this page talks to the API</summary>
        <p>
          The page posts <code>{`{query, tenant_id, session_id}`}</code> to <code>/api/chat/stream</code> and
          renders the server-sent events as they arrive. The session id is generated once per conversation and
          sent on every request, so the API can give the last stored turns to the supervisor and agents.
          <code>answer.delta</code> events fill the live answer; <code>run.finished</code> carries the final
          answer, its status and its citations. A <code>needs_clarification</code> status means the run ended
          with a question back to you, not an answer. Nothing on this page is generated by the browser beyond
          formatting the payloads.
        </p>
      </details>
    </div>
  );
}
