import { useEffect, useState } from "react";

import { errorMessage, getJson, isAbortError } from "../api";
import type { RunSummary } from "../api";
import { CitationChips, ErrorList, StatusBadge, Tag } from "../components/Badges";
import { FeedbackForm } from "../components/Feedback";
import { TENANTS } from "../config";
import {
  formatCostDetail,
  formatDateTime,
  formatLatency,
  formatTokens,
  formatWhen,
} from "../format";
import { tabHref, traceHref } from "../routes";

export function RunDetailPage({
  runId,
  tenant,
  sessionId,
}: {
  runId: string;
  tenant: string | null;
  sessionId: string | null;
}) {
  const tenantId = tenant ?? TENANTS[0];
  const [run, setRun] = useState<RunSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const activeSession = sessionId ?? run?.session_id ?? null;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getJson<RunSummary>(
      `/runs/${encodeURIComponent(runId)}?tenant_id=${encodeURIComponent(tenantId)}`,
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) {
          setRun(data);
        }
      })
      .catch((caught) => {
        if (!isAbortError(caught)) {
          setError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });
    return () => {
      controller.abort();
    };
  }, [runId, tenantId]);

  return (
    <div className="page">
      <p className="back-link">
        <a href={tabHref("runs", tenantId, activeSession)}>Back to runs</a>
      </p>

      {loading ? <p className="muted">Loading run...</p> : null}
      {error !== null ? (
        <div className="error-box" role="alert">
          <strong>Could not load the run</strong>
          <p>{error}</p>
          <p className="muted small">
            A run belongs to one tenant, so another tenant's run answers 404 rather than showing a
            partial record.
          </p>
        </div>
      ) : null}

      {run !== null ? (
        <>
          <section className="surface">
            <div className="surface-head">
              <h2 className="surface-title">Question</h2>
              <StatusBadge status={run.status} />
            </div>
            <p className="answer-text">{run.query}</p>
            <p className="turn-meta">
              run <code>{run.run_id}</code> · session{" "}
              {run.session_id === null ? <span className="muted">none</span> : <code>{run.session_id}</code>}{" "}
              · tenant <code>{run.tenant_id}</code>
            </p>
          </section>

          <section className="surface">
            <h2 className="surface-title">Answer</h2>
            {run.answer !== null && run.answer !== "" ? (
              <p className="turn-text turn-answer">{run.answer}</p>
            ) : (
              <p className="muted">The run has no stored answer.</p>
            )}
            <CitationChips
              runId={run.run_id}
              tenantId={run.tenant_id}
              sessionId={activeSession}
              citations={run.citations}
            />
            <ErrorList errors={run.errors} />
            {!run.trace_complete ? (
              <div className="warning-box">
                The API marked this run's trace incomplete. Some spans may be missing.
              </div>
            ) : null}
            <p className="links">
              <a href={traceHref(run.run_id, run.tenant_id, activeSession)}>
                Open trace
              </a>
              {run.session_id !== null && run.session_id !== "" ? (
                <a
                  href={`#/chat?tenant=${encodeURIComponent(
                    run.tenant_id,
                  )}&session=${encodeURIComponent(run.session_id)}`}
                >
                  Resume conversation
                </a>
              ) : null}
            </p>
          </section>

          <section className="surface">
            <h2 className="surface-title">Run record</h2>
            <dl className="kv-grid">
              <div className="kv">
                <dt>plan</dt>
                <dd>{run.plan ?? "not recorded"}</dd>
              </div>
              <div className="kv">
                <dt>orchestrator</dt>
                <dd>
                  <code>{run.orchestrator}</code>
                </dd>
              </div>
              <div className="kv">
                <dt>provider</dt>
                <dd>
                  {run.provider === null ? (
                    <span className="muted">unknown (written before cost accounting)</span>
                  ) : (
                    <code>{run.provider}</code>
                  )}
                </dd>
              </div>
              <div className="kv">
                <dt>attempts</dt>
                <dd>{run.attempts}</dd>
              </div>
              <div className="kv">
                <dt>tokens</dt>
                <dd>
                  {formatTokens(run.input_tokens)} in / {formatTokens(run.output_tokens)} out
                  {run.cached_input_tokens > 0
                    ? `, ${formatTokens(run.cached_input_tokens)} cached`
                    : ""}
                </dd>
              </div>
              <div className="kv">
                <dt>cost</dt>
                <dd>{formatCostDetail(run.cost_usd)}</dd>
              </div>
              <div className="kv">
                <dt>cache</dt>
                <dd>
                  <Tag tone={run.cache_hit ? "accent" : "neutral"}>
                    {run.cache_hit ? "hit" : "miss"}
                  </Tag>
                </dd>
              </div>
              <div className="kv">
                <dt>latency</dt>
                <dd>{formatLatency(run.started_at, run.ended_at)}</dd>
              </div>
              <div className="kv">
                <dt>started</dt>
                <dd title={formatDateTime(run.started_at)}>{formatWhen(run.started_at)}</dd>
              </div>
              <div className="kv">
                <dt>ended</dt>
                <dd title={formatDateTime(run.ended_at)}>{formatWhen(run.ended_at)}</dd>
              </div>
              <div className="kv">
                <dt>trace</dt>
                <dd>{run.trace_complete ? "complete" : "incomplete"}</dd>
              </div>
            </dl>
          </section>

          <section className="surface">
            <FeedbackForm
              key={run.run_id}
              runId={run.run_id}
              tenantId={run.tenant_id}
              latest={run.latest_feedback}
            />
          </section>
        </>
      ) : null}
    </div>
  );
}
