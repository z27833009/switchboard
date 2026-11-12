import { useEffect, useState } from "react";

import { errorMessage, getJson, isAbortError } from "../api";
import type { RunSummary } from "../api";
import { CitationChips, ErrorList, StatusBadge } from "../components/Badges";
import { formatDateTime, formatLatency, formatTokens } from "../format";

export function RunDetailPage({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getJson<RunSummary>(`/runs/${encodeURIComponent(runId)}`, controller.signal)
      .then((data) => {
        setRun(data);
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
  }, [runId]);

  return (
    <div className="page">
      <section className="panel">
        <div className="panel-header">
          <h2>Run</h2>
          {run !== null ? <StatusBadge status={run.status} /> : null}
        </div>
        {loading ? <p className="muted">Loading...</p> : null}
        {error !== null ? (
          <div className="error-box">
            <strong>Could not load the run</strong>
            <p>{error}</p>
          </div>
        ) : null}
        {run !== null ? (
          <>
            <p className="meta">
              run <code>{run.run_id}</code> tenant <code>{run.tenant_id}</code> orchestrator{" "}
              <code>{run.orchestrator}</code>
            </p>
            <p className="meta">
              started {formatDateTime(run.started_at)} ended {formatDateTime(run.ended_at)} latency{" "}
              {formatLatency(run.started_at, run.ended_at)}
            </p>
            <p className="meta">
              plan {run.plan ?? "-"} attempts {run.attempts} input{" "}
              {formatTokens(run.input_tokens)} tokens output {formatTokens(run.output_tokens)} tokens
              {run.cost_usd !== null ? ` cost $${run.cost_usd.toFixed(4)}` : " no API cost recorded"}
            </p>
            {!run.trace_complete ? (
              <div className="warning-box">
                The API marked this run's trace incomplete. Some spans may be missing.
              </div>
            ) : null}

            <h3>Query</h3>
            <p className="answer-text">{run.query}</p>

            <h3>Answer</h3>
            {run.answer !== null && run.answer !== "" ? (
              <p className="answer-text">{run.answer}</p>
            ) : (
              <p className="muted">The run has no stored answer.</p>
            )}
            <CitationChips runId={run.run_id} citations={run.citations} />
            <ErrorList errors={run.errors} />

            <p className="links">
              <a href={`#/runs/${encodeURIComponent(run.run_id)}/trace`}>Open trace</a>
              <a href="#/runs">Back to runs</a>
            </p>
          </>
        ) : null}
      </section>
    </div>
  );
}
