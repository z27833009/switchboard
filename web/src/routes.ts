import type { Citation } from "./api";

// Hash routing keeps the UI free of a router dependency (design.md section 21).
// The trace route accepts either a span id or a tool audit id so a citation chip
// can select the span without the UI guessing when span_id is null.
export type Route =
  | { name: "chat" }
  | { name: "runs" }
  | { name: "run"; runId: string }
  | { name: "trace"; runId: string; spanId: string | null; auditId: string | null };

export function parseRoute(hash: string): Route {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [pathPart = "", queryPart = ""] = raw.split("?", 2);
  const segments = pathPart.split("/").filter((segment) => segment !== "");
  if (segments.length === 0) {
    return { name: "chat" };
  }
  if (segments.length === 1 && segments[0] === "chat") {
    return { name: "chat" };
  }
  if (segments.length === 1 && segments[0] === "runs") {
    return { name: "runs" };
  }
  if (segments.length === 2 && segments[0] === "runs") {
    return { name: "run", runId: decodeURIComponent(segments[1]) };
  }
  if (segments.length === 3 && segments[0] === "runs" && segments[2] === "trace") {
    const params = new URLSearchParams(queryPart);
    return {
      name: "trace",
      runId: decodeURIComponent(segments[1]),
      spanId: params.get("span"),
      auditId: params.get("audit"),
    };
  }
  return { name: "chat" };
}

export function routeKey(route: Route): string {
  if (route.name === "trace") {
    return `trace:${route.runId}:${route.spanId ?? ""}:${route.auditId ?? ""}`;
  }
  if (route.name === "run") {
    return `run:${route.runId}`;
  }
  return route.name;
}

export function citationHref(runId: string, citation: Citation): string {
  const params = new URLSearchParams();
  if (citation.span_id !== null && citation.span_id !== "") {
    params.set("span", citation.span_id);
  } else if (citation.kind === "tool" && citation.audit_id !== null && citation.audit_id !== "") {
    // Tool spans carry switchboard.tool.audit_id, so the trace page can find
    // the span even when the citation has no span id.
    params.set("audit", citation.audit_id);
  }
  const query = params.toString();
  const suffix = query === "" ? "" : `?${query}`;
  return `#/runs/${encodeURIComponent(runId)}/trace${suffix}`;
}
