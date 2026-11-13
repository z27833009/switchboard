import { useEffect, useState } from "react";

import { errorMessage, getJson, isAbortError } from "../api";
import type { RunSummary, RunsResponse } from "../api";
import { StatusBadge, Tag } from "../components/Badges";
import { formatLatency, formatWhen } from "../format";
import { runHref, traceHref } from "../routes";

// One trace per run (design.md section 12.2), so the trace list is the run list
// with the trajectory fields in front. Opening a row keeps the existing trace
// page, which owns the Timeline and Tree views and the plan graph.
export function TracesPage({
  tenant,
  sessionId,
}: {
  tenant: string;
  sessionId: string | null;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(sessionId !== null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (sessionId === null) {
      setRuns([]);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({
      tenant_id: tenant,
      session_id: sessionId,
      limit: "50",
    });
    getJson<RunsResponse>(`/runs?${params.toString()}`, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setRuns(data.runs);
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
    return () => controller.abort();
  }, [tenant, sessionId, reloadKey]);

  return (
    <div className="page">
      <p className="page-intro">
        Every run stores its spans under one trace. Open a row for the plan graph, the timeline,
        the tree, the attribute pane and the full tool result.
      </p>

      <div className="filter-bar">
        <span className="muted small" role="status">
          {sessionId === null
            ? "No conversation selected"
            : loading
              ? "Loading..."
              : `${runs.length} trace(s) in this conversation`}
        </span>
        <span className="filter-spacer" />
        <button
          type="button"
          className="button button-quiet"
          onClick={() => setReloadKey((value) => value + 1)}
          disabled={loading}
        >
          Refresh
        </button>
      </div>

      {error !== null ? (
        <div className="error-box" role="alert">
          <strong>Could not load traces</strong>
          <p>{error}</p>
        </div>
      ) : null}

      {!loading && error === null && runs.length === 0 ? (
        <div className="empty-state">
          <h2>{sessionId === null ? "No conversation selected" : "No traces in this conversation"}</h2>
          <p className="muted">
            {sessionId === null
              ? "Choose a conversation from the sidebar or start a new chat."
              : "A trace appears once a run in this conversation has happened."}
          </p>
        </div>
      ) : null}

      {runs.length > 0 ? (
        <ul className="trace-list">
          {runs.map((run) => (
            <li key={run.run_id} className="trace-list-item">
              <div className="trace-list-main">
                <a
                  className="trace-list-title row-link"
                  href={traceHref(run.run_id, run.tenant_id, sessionId)}
                >
                  {run.query}
                </a>
                <p className="trace-list-meta">
                  <StatusBadge status={run.status} />
                  <span>plan {run.plan ?? "-"}</span>
                  <span>{formatWhen(run.started_at)}</span>
                  <span>{formatLatency(run.started_at, run.ended_at)}</span>
                  {run.cache_hit ? <Tag tone="accent">cache hit</Tag> : null}
                  {!run.trace_complete ? <Tag tone="warning">trace incomplete</Tag> : null}
                </p>
              </div>
              <div className="trace-list-actions">
                <a href={traceHref(run.run_id, run.tenant_id, sessionId)}>Open trace</a>
                <a href={runHref(run.run_id, run.tenant_id, sessionId)}>Run detail</a>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
