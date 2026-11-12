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

export function formatValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2) ?? String(value);
}
