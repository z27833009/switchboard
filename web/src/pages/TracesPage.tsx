import { useEffect, useState } from "react";

import { errorMessage, getJson, isAbortError } from "../api";
import type { RunSummary, RunsResponse } from "../api";
import { StatusBadge, Tag } from "../components/Badges";
import { TENANTS } from "../config";
import { formatLatency, formatWhen } from "../format";

// One trace per run (design.md section 12.2), so the trace list is the run list
// with the trajectory fields in front. Opening a row keeps the existing trace
// page, which owns the Timeline and Tree views and the plan graph.
export function TracesPage({
  tenant,
  onTenantChange,
}: {
  tenant: string;
  onTenantChange: (tenant: string) => void;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getJson<RunsResponse>(
      `/runs?limit=50&tenant_id=${encodeURIComponent(tenant)}`,
      controller.signal,
    )
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
  }, [tenant, reloadKey]);

  return (
    <div className="page">
      <p className="page-intro">
        Every run stores its spans under one trace. Open a row for the plan graph, the timeline,
        the tree, the attribute pane and the full tool result.
      </p>

      <div className="filter-bar">
        <label className="field field-inline">
          <span className="field-label">Tenant</span>
          <select value={tenant} onChange={(event) => onTenantChange(event.target.value)}>
            {TENANTS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <span className="filter-spacer" />
        <span className="muted small" role="status">
          {loading ? "Loading..." : `${runs.length} most recent trace(s) for ${tenant}`}
        </span>
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
          <h2>No traces yet</h2>
          <p className="muted">
            A trace exists once a run has happened. Send a question in Chat, then open it here.
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
                  href={`#/runs/${encodeURIComponent(run.run_id)}/trace?tenant=${encodeURIComponent(
                    run.tenant_id,
                  )}`}
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
                <a
                  href={`#/runs/${encodeURIComponent(run.run_id)}/trace?tenant=${encodeURIComponent(
                    run.tenant_id,
                  )}`}
                >
                  Open trace
                </a>
                <a
                  href={`#/runs/${encodeURIComponent(run.run_id)}?tenant=${encodeURIComponent(
                    run.tenant_id,
                  )}`}
                >
                  Run detail
                </a>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
