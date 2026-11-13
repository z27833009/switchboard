import type { Citation } from "./api";

// Hash routing keeps the UI free of a router dependency (design.md section 21).
// The trace route accepts either a span id or a tool audit id so a citation chip
// can select the span without the UI guessing when span_id is null.
// Run and trace reads are tenant scoped: the API requires tenant_id, and the
// route carries it so a link never falls back to another tenant's run.
// `view` is optional and only picks the initial span view (tree or timeline);
// the page switch does not rewrite the hash, so a screenshot or a shared link
// can still open a named view.
export type TraceView = "tree" | "timeline";

export type Route =
  | { name: "chat" }
  | { name: "runs" }
  | { name: "run"; runId: string; tenant: string | null }
  | {
      name: "trace";
      runId: string;
      tenant: string | null;
      spanId: string | null;
      auditId: string | null;
      view: TraceView | null;
    };

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
    const params = new URLSearchParams(queryPart);
    return {
      name: "run",
      runId: decodeURIComponent(segments[1]),
      tenant: params.get("tenant"),
    };
  }
  if (segments.length === 3 && segments[0] === "runs" && segments[2] === "trace") {
    const params = new URLSearchParams(queryPart);
    const viewParam = params.get("view");
    return {
      name: "trace",
      runId: decodeURIComponent(segments[1]),
      tenant: params.get("tenant"),
      spanId: params.get("span"),
      auditId: params.get("audit"),
      view: viewParam === "timeline" ? "timeline" : viewParam === "tree" ? "tree" : null,
    };
  }
  return { name: "chat" };
}

export function routeKey(route: Route): string {
  if (route.name === "trace") {
    return `trace:${route.runId}:${route.tenant ?? ""}:${route.spanId ?? ""}:${route.auditId ?? ""}:${route.view ?? ""}`;
  }
  if (route.name === "run") {
    return `run:${route.runId}:${route.tenant ?? ""}`;
  }
  return route.name;
}

export function citationHref(runId: string, tenantId: string, citation: Citation): string {
  const params = new URLSearchParams();
  params.set("tenant", tenantId);
  if (citation.span_id !== null && citation.span_id !== "") {
    params.set("span", citation.span_id);
  } else if (citation.kind === "tool" && citation.audit_id !== null && citation.audit_id !== "") {
    // Tool spans carry switchboard.tool.audit_id, so the trace page can find
    // the span even when the citation has no span id.
    params.set("audit", citation.audit_id);
  }
  return `#/runs/${encodeURIComponent(runId)}/trace?${params.toString()}`;
}
