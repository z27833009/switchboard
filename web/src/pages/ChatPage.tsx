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
    </section>
  );
}

export function ChatPage() {
  const [tenant, setTenant] = useState(TENANTS[0]);
  const [query, setQuery] = useState("");
  const [steps, setSteps] = useState<StepEvent[]>([]);
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
    setResult(null);
    setError(null);
    setEndedWithoutResult(false);
    let sawRunFinished = false;
    try {
      await postChatStream(
        { query: trimmed, tenant_id: tenant },
        (step) => {
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
      {result !== null ? <AnswerPanel result={result} /> : null}

      {steps.length === 0 && !streaming && error === null && !endedWithoutResult ? (
        <section className="panel">
          <p className="muted">
            Send a question to stream the run. The answer and its citations appear after run.finished.
          </p>
        </section>
      ) : null}

      <details className="panel raw-details">
        <summary>How this page talks to the API</summary>
        <p>
          The page posts <code>{`{query, tenant_id}`}</code> to <code>/api/chat/stream</code> and renders
          the server-sent events as they arrive. Nothing on this page is generated by the browser beyond
          formatting the payloads.
        </p>
      </details>
    </div>
  );
}
