import type { ReactNode } from "react";

import type { Citation, ErrorRecord } from "../api";
import { citationHref } from "../routes";
import { AlertIcon, CheckIcon, ClockIcon, DotIcon } from "./Icons";

const WARNING_STATUSES = new Set([
  "partial",
  "needs_clarification",
  "timeout",
  "provider_error",
  "auth_error",
  "waiting_approval",
]);

export type Tone = "ok" | "running" | "warning" | "neutral";

export function statusTone(status: string): Tone {
  if (status === "ok") {
    return "ok";
  }
  if (status === "running") {
    return "running";
  }
  if (WARNING_STATUSES.has(status)) {
    return "warning";
  }
  return "neutral";
}

// The badge never relies on colour alone: each tone has its own glyph as well
// as its label, so a status stays readable in forced colours and for a reader
// who cannot separate the green from the amber (WCAG 1.4.1).
function ToneIcon({ tone }: { tone: Tone }) {
  const className = "badge-icon";
  if (tone === "ok") {
    return <CheckIcon className={className} />;
  }
  if (tone === "warning") {
    return <AlertIcon className={className} />;
  }
  if (tone === "running") {
    return <ClockIcon className={className} />;
  }
  return <DotIcon className={className} />;
}

export function StatusBadge({ status }: { status: string }) {
  const tone = statusTone(status);
  return (
    <span className={`badge badge-${tone}`}>
      <ToneIcon tone={tone} />
      <span className="badge-label">{status.replace(/_/g, " ")}</span>
    </span>
  );
}

export function Tag({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: Tone | "accent";
  title?: string;
}) {
  return (
    <span className={`tag tag-${tone}`} title={title}>
      {children}
    </span>
  );
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
    return <p className="muted small">No citations were returned for this answer.</p>;
  }
  return (
    <div className="chips" aria-label="Citations">
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
