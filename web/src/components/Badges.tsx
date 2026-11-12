import type { Citation, ErrorRecord } from "../api";
import { citationHref } from "../routes";

const WARNING_STATUSES = new Set([
  "partial",
  "needs_clarification",
  "timeout",
  "provider_error",
  "auth_error",
  "waiting_approval",
]);

export function StatusBadge({ status }: { status: string }) {
  let tone = "neutral";
  if (status === "ok") {
    tone = "ok";
  } else if (status === "running") {
    tone = "running";
  } else if (WARNING_STATUSES.has(status)) {
    tone = "warning";
  }
  return <span className={`badge badge-${tone}`}>{status}</span>;
}

export function ErrorList({ errors }: { errors: ErrorRecord[] }) {
  if (errors.length === 0) {
    return null;
  }
  return (
    <div className="error-box">
      <strong>Errors reported by the API</strong>
      <ul>
        {errors.map((error, index) => (
          <li key={`${error.kind}-${index}`}>
            <code>{error.kind}</code> {error.message}
            {error.stage !== null ? ` (stage ${error.stage})` : ""}
            {error.retryable ? " (retryable)" : ""}
            {Object.keys(error.details).length > 0 ? (
              <details>
                <summary>details</summary>
                <pre>{JSON.stringify(error.details, null, 2)}</pre>
              </details>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CitationChips({
  runId,
  tenantId,
  citations,
}: {
  runId: string;
  tenantId: string;
  citations: Citation[];
}) {
  if (citations.length === 0) {
    return <p className="muted">No citations were returned for this answer.</p>;
  }
  return (
    <div className="chips">
      {citations.map((citation, index) => {
        const label = citation.kind === "tool" ? `tool ${citation.ref}` : `doc ${citation.ref}`;
        return (
          <a
            key={`${citation.kind}-${citation.ref}-${index}`}
            className={`chip chip-${citation.kind}`}
            href={citationHref(runId, tenantId, citation)}
            title={`${citation.source_id}\n${citation.snippet}`}
          >
            {label}
          </a>
        );
      })}
    </div>
  );
}
