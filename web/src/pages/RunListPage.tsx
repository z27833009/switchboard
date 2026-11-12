import { useEffect, useState } from "react";

import { errorMessage, getJson, isAbortError } from "../api";
import type { RunSummary, RunsResponse } from "../api";
import { StatusBadge } from "../components/Badges";
import { TENANTS } from "../config";
import { formatDateTime, formatLatency, formatTokens } from "../format";

export function RunListPage() {
  // Run reads require a tenant, so the list always has one selected.
  const [tenant, setTenant] = useState(TENANTS[0]);
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
        setRuns(data.runs);
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
  }, [tenant, reloadKey]);

  return (
    <div className="page">
      <section className="panel">
        <div className="panel-header">
          <h2>Runs</h2>
          <button type="button" onClick={() => setReloadKey((value) => value + 1)} disabled={loading}>
            Refresh
          </button>
        </div>
        <div className="filters">
          <label className="field">
            <span>Tenant</span>
            <select value={tenant} onChange={(event) => setTenant(event.target.value)}>
              {TENANTS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <span className="muted">{loading ? "Loading..." : `${runs.length} run(s)`}</span>
        </div>
        {error !== null ? (
          <div className="error-box">
            <strong>Could not load runs</strong>
            <p>{error}</p>
          </div>
        ) : null}
        {!loading && error === null && runs.length === 0 ? (
          <p className="muted">The API returned no runs for this filter.</p>
        ) : null}
        {runs.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Query</th>
                  <th>Plan</th>
                  <th>Status</th>
                  <th>Tokens in / out</th>
                  <th>Latency</th>
                  <th>Trace</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.run_id}>
                    <td className="nowrap">{formatDateTime(run.started_at)}</td>
                    <td>
                      <a
                        href={`#/runs/${encodeURIComponent(run.run_id)}?tenant=${encodeURIComponent(run.tenant_id)}`}
                      >
                        {run.query}
                      </a>
                      <span className="run-id">{run.run_id}</span>
                    </td>
                    <td>{run.plan ?? "-"}</td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="nowrap">
                      {formatTokens(run.input_tokens)} / {formatTokens(run.output_tokens)}
                    </td>
                    <td className="nowrap">{formatLatency(run.started_at, run.ended_at)}</td>
                    <td className="nowrap">
                      <a
                        href={`#/runs/${encodeURIComponent(run.run_id)}/trace?tenant=${encodeURIComponent(run.tenant_id)}`}
                      >
                        trace
                      </a>
                      {!run.trace_complete ? <span className="badge badge-warning">incomplete</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
