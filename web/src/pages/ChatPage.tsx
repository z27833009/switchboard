import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import { useEffect, useRef, useState } from "react";

import {
  asNumber,
  asString,
  asStringArray,
  errorMessage,
  getJson,
  isAbortError,
  postChatStream,
  toChatResponse,
} from "../api";
import type { ChatResponse, RunSummary, RunsResponse, StepEvent } from "../api";
import { CitationChips, ErrorList, StatusBadge, Tag, statusTone } from "../components/Badges";
import { AlertIcon, ClockIcon } from "../components/Icons";
import { Markdown } from "../components/Markdown";
import { formatCostDetail, formatDuration, formatLatency, formatTokens } from "../format";
import { runHref, traceHref } from "../routes";
import { newSessionId } from "../session";

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

function StepList({ steps }: { steps: StepEvent[] }) {
  if (steps.length === 0) {
    return <p className="muted small">No live steps are stored for this turn.</p>;
  }
  return (
    <ol className="step-list">
      {steps.map((step, index) => (
        <li key={`${step.type}-${index}`} className="step">
          <div className="step-line">
            <span className="step-type">{step.type}</span>
            <span className="step-text">{stepSummary(step)}</span>
          </div>
          <details className="step-payload">
            <summary>payload</summary>
            <pre>{JSON.stringify(step, null, 2)}</pre>
          </details>
        </li>
      ))}
    </ol>
  );
}

interface TurnView {
  id: string;
  question: string;
  result: ChatResponse | null;
  streamed: string;
  steps: StepEvent[];
  error: string | null;
  endedWithoutResult: boolean;
  streaming: boolean;
}

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// A stored run row has the same content the stream's final payload had, but
// flattened. The transcript is rebuilt from these rows, never from browser
// memory, so a resumed conversation shows exactly what was stored.
function runToChatResponse(run: RunSummary): ChatResponse {
  return {
    run_id: run.run_id,
    tenant_id: run.tenant_id,
    session_id: run.session_id,
    status: run.status,
    answer: run.answer,
    plan:
      run.plan === null
        ? null
        : { agents: run.plan.split("+").filter((agent) => agent !== ""), reason: "" },
    citations: run.citations,
    usage: {
      input_tokens: run.input_tokens,
      cached_input_tokens: run.cached_input_tokens,
      output_tokens: run.output_tokens,
    },
    cost_usd: run.cost_usd,
    cache_hit: run.cache_hit,
    attempts: run.attempts,
    errors: run.errors,
    started_at: run.started_at,
    ended_at: run.ended_at,
    trace_incomplete: !run.trace_complete,
  };
}

function runToTurn(run: RunSummary): TurnView {
  return {
    id: run.run_id,
    question: run.query,
    result: runToChatResponse(run),
    streamed: "",
    steps: [],
    error: null,
    endedWithoutResult: false,
    streaming: false,
  };
}

function applyStep(turn: TurnView, step: StepEvent): TurnView {
  if (step.type === "answer.delta") {
    return { ...turn, streamed: turn.streamed + asString(step.text) };
  }
  const next: TurnView = { ...turn, steps: [...turn.steps, step] };
  if (step.type === "run.finished") {
    return { ...next, result: toChatResponse(step), streaming: false };
  }
  return next;
}

function gateFromSteps(steps: StepEvent[]): { verdict: string; action: string } | null {
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    if (step.type === "gate.checked") {
      return { verdict: asString(step.verdict), action: asString(step.action) };
    }
  }
  return null;
}

function planFromSteps(steps: StepEvent[]): string | null {
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    if (step.type === "plan.decided") {
      const plan = asString(step.plan);
      if (plan !== "") {
        return plan;
      }
    }
  }
  return null;
}

function RunSummaryBar({ turn }: { turn: TurnView }) {
  const result = turn.result;
  const failed = turn.error !== null || turn.endedWithoutResult;
  const plan =
    result !== null && result.plan !== null
      ? result.plan.agents.join(" + ")
      : planFromSteps(turn.steps);
  const gate = gateFromSteps(turn.steps);
  const status = result === null ? (failed ? "error" : "running") : result.status;
  return (
    <section className="run-summary" aria-label="Latest run summary">
      <dl className="run-summary-row">
        <div className="run-summary-item">
          <dt>plan</dt>
          <dd>{plan === null || plan === "" ? "not decided" : plan}</dd>
        </div>
        <div className="run-summary-item">
          <dt>status</dt>
          <dd>
            {result === null && !failed ? (
              <span className={`badge badge-${statusTone("running")}`}>
                <ClockIcon className="badge-icon" />
                <span className="badge-label">running</span>
              </span>
            ) : result === null ? (
              <span className={`badge badge-${statusTone("partial")}`}>
                <AlertIcon className="badge-icon" />
                <span className="badge-label">request failed</span>
              </span>
            ) : (
              <StatusBadge status={status} />
            )}
          </dd>
        </div>
        <div className="run-summary-item">
          <dt>latency</dt>
          <dd>
            {result === null ? (failed ? "not finished" : "running") : formatLatency(result.started_at, result.ended_at)}
          </dd>
        </div>
        <div className="run-summary-item">
          <dt>tokens</dt>
          <dd>
            {result === null
              ? "-"
              : `${formatTokens(result.usage.input_tokens)} in / ${formatTokens(
                  result.usage.output_tokens,
                )} out`}
          </dd>
        </div>
        <div className="run-summary-item">
          <dt>cost</dt>
          <dd title={formatCostDetail(result?.cost_usd ?? null)}>
            {result === null ? "-" : formatCostDetail(result.cost_usd)}
          </dd>
        </div>
        <div className="run-summary-item">
          <dt>gate</dt>
          <dd>
            {gate === null ? (
              <span className="muted">not recorded</span>
            ) : (
              <Tag tone={gate.verdict === "pass" ? "ok" : "warning"}>
                {gate.verdict}
                {gate.action === "" ? "" : ` (${gate.action})`}
              </Tag>
            )}
          </dd>
        </div>
        <div className="run-summary-item">
          <dt>cache</dt>
          <dd>{result === null ? "-" : result.cache_hit ? "hit" : "miss"}</dd>
        </div>
      </dl>
      <details className="run-steps">
        <summary>Steps ({turn.steps.length})</summary>
        <StepList steps={turn.steps} />
      </details>
    </section>
  );
}

function TurnArticle({
  turn,
}: {
  turn: TurnView;
}) {
  const result = turn.result;
  const clarifying = result !== null && result.status === "needs_clarification";
  const answerText =
    result !== null && result.answer !== null && result.answer !== ""
      ? result.answer
      : turn.streamed;
  return (
    <>
      <article className="turn turn-user">
        <p className="turn-author">You</p>
        <p className="turn-text">{turn.question}</p>
      </article>
      <article
        className={`turn turn-assistant${clarifying ? " turn-clarify" : ""}`}
        aria-label={clarifying ? "Clarification question" : "Assistant answer"}
      >
        <div className="turn-head">
          <p className="turn-author">Assistant</p>
          {result === null ? (
            <span className={`badge badge-${statusTone("running")}`}>
              <ClockIcon className="badge-icon" />
              <span className="badge-label">streaming</span>
            </span>
          ) : (
            <StatusBadge status={result.status} />
          )}
          {result !== null && result.cache_hit ? <Tag tone="accent">cache hit</Tag> : null}
          {result !== null && result.attempts > 1 ? (
            <Tag tone="neutral">attempt {result.attempts}</Tag>
          ) : null}
        </div>

        {clarifying ? (
          <>
            <p className="clarify-question">
              {answerText === "" ? "The agent asked for clarification without a question." : answerText}
            </p>
            <p className="muted small">
              The run ended as <code>needs_clarification</code>, so this is a question rather than
              a factual answer and it has no citations. Answer in the same conversation and the
              stored turn is used as context for the next request.
            </p>
          </>
        ) : answerText !== "" ? (
          <div className="turn-answer">
            <Markdown text={answerText} />
          </div>
        ) : turn.streaming ? (
          <p className="muted">Waiting for the first token...</p>
        ) : (
          <p className="muted">The API returned no answer for this status.</p>
        )}

        {result !== null && !clarifying ? (
          <CitationChips
            runId={result.run_id}
            tenantId={result.tenant_id}
            sessionId={result.session_id}
            citations={result.citations}
          />
        ) : null}

        {result !== null && result.errors.length > 0 ? (
          result.status === "ok" ? (
            <details className="turn-error-details">
              <summary>{result.errors.length} corrected error(s)</summary>
              <ErrorList errors={result.errors} />
            </details>
          ) : (
            <ErrorList errors={result.errors} />
          )
        ) : null}

        {turn.error !== null ? (
          <div className="error-box" role="alert">
            <strong>Request failed</strong>
            <p>{turn.error}</p>
          </div>
        ) : null}
        {turn.endedWithoutResult ? (
          <div className="error-box" role="alert">
            <strong>The stream ended without run.finished</strong>
            <p>
              The API closed the event stream before the final payload. The run may still be
              running; check the <a href="#/runs">run list</a> for its stored result.
            </p>
          </div>
        ) : null}
        {result !== null && result.trace_incomplete ? (
          <p className="warning-text">
            The API marked this run's trace incomplete because the span flush did not finish. The
            trace page shows only the spans that were stored.
          </p>
        ) : null}

        {result !== null ? (
          <>
            <p className="turn-meta">
              run{" "}
              <a href={runHref(result.run_id, result.tenant_id, result.session_id)} title={result.run_id}>
                <code>{result.run_id.slice(0, 8)}</code>
              </a>{" "}
              · {formatTokens(result.usage.input_tokens)} in /{" "}
              {formatTokens(result.usage.output_tokens)} out ·{" "}
              {formatCostDetail(result.cost_usd)} ·{" "}
              <a href={traceHref(result.run_id, result.tenant_id, result.session_id)}>open trace</a>
            </p>
          </>
        ) : null}
      </article>
    </>
  );
}

export function ChatPage({
  tenant,
  sessionId: routeSessionId,
  onTitleChange,
  onRunFinished,
  onSessionChange,
}: {
  tenant: string;
  sessionId: string | null;
  onTitleChange: (title: string | null) => void;
  onRunFinished: () => void;
  onSessionChange: (sessionId: string) => void;
}) {
  const [sessionId, setSessionId] = useState<string>(() => routeSessionId ?? newSessionId());
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(routeSessionId !== null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return () => {
      controllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    onSessionChange(sessionId);
  }, [onSessionChange, sessionId]);

  useEffect(() => {
    if (routeSessionId === null) {
      return;
    }
    const controller = new AbortController();
    setHistoryLoading(true);
    setHistoryError(null);
    const params = new URLSearchParams({
      tenant_id: tenant,
      session_id: routeSessionId,
      limit: "50",
    });
    getJson<RunsResponse>(`/runs?${params.toString()}`, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) {
          return;
        }
        // The API lists newest first; a transcript reads oldest first.
        const stored = [...data.runs].reverse();
        setTurns(
          stored
            .filter((run) => run.answer !== null && run.answer !== "")
            .map((run) => runToTurn(run)),
        );
        setSessionId(routeSessionId);
      })
      .catch((caught) => {
        if (!isAbortError(caught)) {
          setHistoryError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setHistoryLoading(false);
        }
      });
    return () => controller.abort();
  }, [tenant, routeSessionId]);

  useEffect(() => {
    const element = scrollRef.current;
    if (element === null) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [turns]);

  const firstQuestion = turns[0]?.question ?? null;
  useEffect(() => {
    onTitleChange(firstQuestion === null ? null : firstQuestion.trim() || null);
  }, [firstQuestion, onTitleChange]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (trimmed === "" || busy) {
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    const turnId = newId();
    setBusy(true);
    setQuery("");
    setTurns((current) => [
      ...current,
      {
        id: turnId,
        question: trimmed,
        result: null,
        streamed: "",
        steps: [],
        error: null,
        endedWithoutResult: false,
        streaming: true,
      },
    ]);
    let sawRunFinished = false;
    try {
      await postChatStream(
        { query: trimmed, tenant_id: tenant, session_id: sessionId },
        (step) => {
          if (step.type === "run.finished") {
            sawRunFinished = true;
            onRunFinished();
          }
          setTurns((current) =>
            current.map((turn) => (turn.id === turnId ? applyStep(turn, step) : turn)),
          );
        },
        controller.signal,
      );
      if (!sawRunFinished && !controller.signal.aborted) {
        setTurns((current) =>
          current.map((turn) =>
            turn.id === turnId
              ? { ...turn, streaming: false, endedWithoutResult: true }
              : turn,
          ),
        );
      }
    } catch (caught) {
      if (!isAbortError(caught)) {
        const message = errorMessage(caught);
        setTurns((current) =>
          current.map((turn) =>
            turn.id === turnId ? { ...turn, streaming: false, error: message } : turn,
          ),
        );
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
      }
      setBusy(false);
      setTurns((current) =>
        current.map((turn) =>
          turn.id === turnId && turn.streaming ? { ...turn, streaming: false } : turn,
        ),
      );
    }
  }

  function onComposerKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    // Enter sends, Shift+Enter adds a line. During an IME composition Enter
    // confirms the candidate, so it must not submit; keyCode 229 covers WebKit
    // delivering compositionend before the confirming keydown.
    event.preventDefault();
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) {
      return;
    }
    event.currentTarget.form?.requestSubmit();
  }

  const latest = turns.length === 0 ? null : turns[turns.length - 1];

  return (
    <div className="chat">
      <div className="chat-transcript" ref={scrollRef}>
        <div className="chat-column">
          {historyLoading ? <p className="muted">Loading the stored conversation...</p> : null}
          {historyError !== null ? (
            <div className="error-box" role="alert">
              <strong>Could not load the stored conversation</strong>
              <p>{historyError}</p>
              <p className="muted small">
                Nothing is shown rather than a made-up transcript. New questions still start from
                the stored session.
              </p>
            </div>
          ) : null}
          {turns.length === 0 && !historyLoading && historyError === null ? (
            <section className="empty-state">
              <h2>Ask the local workspace</h2>
              <p className="muted">
                The answer streams in, and each run keeps its plan, live steps, citations and
                trace. Follow-ups such as "And in April?" reuse this conversation through its
                session id.
              </p>
              <p className="muted small">
                session <code>{sessionId}</code>
              </p>
            </section>
          ) : null}
          {turns.map((turn) => (
            <TurnArticle key={turn.id} turn={turn} />
          ))}
        </div>
      </div>
      <div className="chat-footer">
        <div className="chat-column">
          {latest !== null ? <RunSummaryBar turn={latest} /> : null}
          <form className="composer" onSubmit={handleSubmit}>
            <label className="visually-hidden" htmlFor="composer-input">
              Message
            </label>
            <textarea
              id="composer-input"
              className="composer-input"
              value={query}
              rows={1}
              placeholder="Ask about orders, products or policies"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={onComposerKeyDown}
              aria-describedby="composer-hint"
            />
            <button
              type="submit"
              className="composer-send"
              disabled={busy || query.trim() === ""}
            >
              {busy ? "Running..." : "Send"}
            </button>
          </form>
          <p id="composer-hint" className="muted small composer-hint">
            Enter sends, Shift+Enter adds a line. This build has no sign-in; the tenant is{" "}
            <code>{tenant}</code>.
          </p>
        </div>
      </div>
    </div>
  );
}
