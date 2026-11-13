export function formatDateTime(value: string | null): string {
  if (value === null || value === "") {
    return "not recorded";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

// A compact time for a list row: the time today, otherwise a short date. The
// full timestamp stays available through formatDateTime where there is room.
export function formatWhen(value: string | null): string {
  if (value === null || value === "") {
    return "not recorded";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) {
    return "-";
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatLatency(start: string | null, end: string | null): string {
  if (start === null || end === null) {
    return "not finished";
  }
  const started = new Date(start).getTime();
  const ended = new Date(end).getTime();
  if (Number.isNaN(started) || Number.isNaN(ended) || ended < started) {
    return "-";
  }
  return formatDuration(ended - started);
}

export function formatTokens(value: number): string {
  return value.toLocaleString("en-US");
}

// A run whose provider has no price row reports null, which is "not priced",
// not zero. A local model has an explicit zero row and is labelled as such in
// the reports, so the same wording is used here.
export function formatCost(value: number | null): string {
  if (value === null) {
    return "not priced";
  }
  return `$${value.toFixed(4)}`;
}

export function formatCostDetail(value: number | null): string {
  if (value === null) {
    return "no price row for this provider and model";
  }
  if (value === 0) {
    return "no API cost (local or zero-priced model)";
  }
  return `$${value.toFixed(4)}`;
}

export function formatValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2) ?? String(value);
}
