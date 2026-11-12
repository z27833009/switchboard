import { useState } from "react";

import { errorMessage, postJson } from "../api";
import type { FeedbackSummary } from "../api";
import { formatDateTime } from "../format";

// Matches FEEDBACK_NOTE_MAX_CHARS in src/switchboard/api/schemas.py and the
// CHECK constraint in docker/postgres/init/05-feedback.sql.
const NOTE_MAX_CHARS = 2000;

function ratingLabel(rating: number): string {
  return rating === -1 ? "negative (thumbs down)" : "positive (thumbs up)";
}

export function FeedbackForm({
  runId,
  tenantId,
  latest,
}: {
  runId: string;
  tenantId: string;
  latest: FeedbackSummary | null;
}) {
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState<FeedbackSummary | null>(latest);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(rating: -1 | 1) {
    if (saving) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // The tenant is part of the request, and the API answers 404 for a run
      // that belongs to another tenant, so a rating can never cross tenants.
      const created = await postJson<FeedbackSummary>(
        `/runs/${encodeURIComponent(runId)}/feedback?tenant_id=${encodeURIComponent(tenantId)}`,
        { rating, note: note.trim() === "" ? null : note },
      );
      setSaved(created);
      setNote("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="feedback">
      <h3>Rate this answer</h3>
      <p className="meta">
        run <code>{runId}</code> tenant <code>{tenantId}</code>
      </p>
      <label className="field">
        <span>Note (optional, at most {NOTE_MAX_CHARS} characters)</span>
        <textarea
          value={note}
          rows={2}
          maxLength={NOTE_MAX_CHARS}
          placeholder="What was wrong, or what would have made this better?"
          onChange={(event) => setNote(event.target.value)}
          disabled={saving}
        />
      </label>
      <p className="feedback-actions">
        <button type="button" onClick={() => void send(1)} disabled={saving}>
          Thumbs up
        </button>
        <button type="button" onClick={() => void send(-1)} disabled={saving}>
          Thumbs down
        </button>
        {saving ? <span className="muted">Saving...</span> : null}
      </p>
      {error !== null ? (
        <div className="error-box">
          <strong>Feedback was not saved</strong>
          <p>{error}</p>
        </div>
      ) : null}
      {saved !== null ? (
        <p className="feedback-saved">
          Latest feedback: {ratingLabel(saved.rating)} at {formatDateTime(saved.created_at)}
          {saved.note !== null && saved.note !== "" ? `, note: ${saved.note}` : ""}
        </p>
      ) : (
        <p className="muted">No feedback stored for this run yet.</p>
      )}
      <p className="muted">
        Feedback is append-only: a new rating adds a row and becomes the run's current verdict.
        Negative ratings are what <code>switchboard eval harvest</code> reads.
      </p>
    </section>
  );
}
