import { useEffect, useId, useMemo, useState } from "react";

import { errorMessage, getJson, isAbortError } from "../api";
import type { RunSummary, RunsResponse } from "../api";
import { StatusBadge, Tag } from "../components/Badges";
import { formatCost, formatLatency, formatTokens, formatWhen } from "../format";
import { runHref, traceHref } from "../routes";

const STATUSES = [
  "ok",
  "partial",
  "needs_clarification",
  "timeout",
  "provider_error",
  "auth_error",
  "running",
];

export function RunListPage({
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
  const [status, setStatus] = useState("all");
  const [plan, setPlan] = useState("all");
  const captionId = useId();

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
    return () => {
      controller.abort();
    };
  }, [tenant, sessionId, reloadKey]);

  // Plans come from the loaded rows, so the filter never offers a plan label
  // that no run actually has.
  const plans = useMemo(
    () =>
      Array.from(new Set(runs.map((run) => run.plan).filter((value): value is string => value !== null))).sort(),
    [runs],
  );
  const visible = runs.filter(
    (run) => (status === "all" || run.status === status) && (plan === "all" || run.plan === plan),
  );

  return (
    <div className="page">
      <div className="filter-bar">
        <label className="field field-inline">
          <span className="field-label">Status</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">all statuses</option>
            {STATUSES.map((item) => (
              <option key={item} value={item}>
                {item.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-inline">
          <span className="field-label">Plan</span>
          <select value={plan} onChange={(event) => setPlan(event.target.value)}>
            <option value="all">all plans</option>
            {plans.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <span className="filter-spacer" />
        <span className="muted small" role="status">
          {sessionId === null
            ? "No conversation selected"
            : loading
              ? "Loading..."
              : `${visible.length} of the latest ${runs.length} run(s) in this conversation`}
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
          <strong>Could not load runs</strong>
          <p>{error}</p>
        </div>
      ) : null}
      {!loading && error === null && runs.length === 0 ? (
        <div className="empty-state">
          <h2>{sessionId === null ? "No conversation selected" : "No runs in this conversation"}</h2>
          <p className="muted">
            {sessionId === null
              ? "Choose a conversation from the sidebar or start a new chat."
              : "Send a question in Chat and its run appears here."}
          </p>
        </div>
      ) : null}
      {!loading && error === null && runs.length > 0 && visible.length === 0 ? (
        <p className="muted">No run matches these filters.</p>
      ) : null}

      {visible.length > 0 ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-labelledby={captionId}>
          <table className="table">
            <caption id={captionId} className="visually-hidden">
              Runs of this conversation, newest first
            </caption>
            <thead>
              <tr>
                <th scope="col">Query</th>
                <th scope="col">Plan</th>
                <th scope="col">Status</th>
                <th scope="col">Tokens in / out</th>
                <th scope="col">Latency</th>
                <th scope="col">Cost</th>
                <th scope="col">Started</th>
                <th scope="col">Trace</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((run) => (
                <tr key={run.run_id}>
                  <th scope="row" data-label="Query">
                    <a
                      className="row-link"
                      href={runHref(run.run_id, run.tenant_id, sessionId)}
                    >
                      {run.query}
                    </a>
                    <span className="run-id">{run.run_id}</span>
                  </th>
                  <td data-label="Plan">{run.plan ?? "-"}</td>
                  <td data-label="Status">
                    <StatusBadge status={run.status} />
                  </td>
                  <td data-label="Tokens in / out" className="nowrap">
                    {formatTokens(run.input_tokens)} / {formatTokens(run.output_tokens)}
                  </td>
                  <td data-label="Latency" className="nowrap">
                    {formatLatency(run.started_at, run.ended_at)}
                  </td>
                  <td data-label="Cost" className="nowrap">
                    {formatCost(run.cost_usd)}
                  </td>
                  <td data-label="Started" className="nowrap">
                    {formatWhen(run.started_at)}
                  </td>
                  <td data-label="Trace" className="nowrap">
                    <a href={traceHref(run.run_id, run.tenant_id, sessionId)}>
                      trace
                    </a>
                    {run.cache_hit ? <Tag tone="accent">cache</Tag> : null}
                    {!run.trace_complete ? <Tag tone="warning">incomplete</Tag> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
