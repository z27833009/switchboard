import { useState } from "react";

import { ApiError, decideApproval, errorMessage } from "../api";
import type { ChatResponse, PendingApproval } from "../api";
import { formatValue } from "../format";

/**
 * The compact approval card for a run that paused before a write or ask tool
 * (T3.01, AC-15b). Approve and reject post to the API with the run id and the
 * tenant only; the tool name and arguments shown here come from the run row, so
 * this card can never approve something the graph did not ask for.
 */
export function ApprovalCard({
  runId,
  tenantId,
  pending,
  onDecided,
}: {
  runId: string;
  tenantId: string;
  pending: PendingApproval;
  onDecided: (result: ChatResponse) => void;
}) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function decide(action: "approve" | "reject") {
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      const result = await decideApproval(runId, tenantId, action);
      onDecided(result);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setNotice(
          "This run is no longer waiting for approval. The card below is the state " +
            "the API returned; reload to see the current run and trace.",
        );
      }
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="surface approval-card" aria-label="Tool approval">
      <div className="surface-head">
        <h2 className="surface-title">Approval needed</h2>
        <span className="tag tag-warning">waiting for you</span>
      </div>
      <p className="approval-lead">
        This run paused before running a tool. Nothing has been executed yet.
      </p>
      <dl className="kv-grid">
        <div className="kv">
          <dt>tool</dt>
          <dd>
            <code>{pending.tool}</code>
          </dd>
        </div>
        <div className="kv">
          <dt>why</dt>
          <dd>{pending.reason || "the tool needs human approval"}</dd>
        </div>
      </dl>
      <h3 className="approval-subtitle">Arguments</h3>
      <pre className="approval-args">{formatValue(pending.arguments)}</pre>
      {notice !== null ? (
        <div className="warning-box" role="status">
          {notice}
        </div>
      ) : null}
      {error !== null ? (
        <div className="error-box" role="alert">
          <strong>The decision was not applied</strong>
          <p>{error}</p>
        </div>
      ) : null}
      <div className="approval-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={busy !== null}
          onClick={() => void decide("approve")}
        >
          {busy === "approve" ? "Approving..." : "Approve and run"}
        </button>
        <button
          type="button"
          className="button"
          disabled={busy !== null}
          onClick={() => void decide("reject")}
        >
          {busy === "reject" ? "Rejecting..." : "Reject"}
        </button>
        <span className="muted small">
          Approving runs the tool exactly once; rejecting writes nothing and ends the run.
        </span>
      </div>
    </section>
  );
}
