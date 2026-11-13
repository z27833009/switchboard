// Pure helpers for the trace page: span flattening, the timeline window, span
// markers and the plan graph. They read the payloads of GET /runs/{id} and
// GET /runs/{id}/trace unchanged, so the tree, the timeline and the graph all
// render the same stored spans (design.md section 12.3). Nothing here invents
// an agent, a node or an edge: every node is derived from a stored span or
// from the run's recorded plan, and a missing span is reported, not filled in.

import type { RunSummary, TraceSpan } from "./api";

export interface FlatSpan {
  span: TraceSpan;
  depth: number;
}

export interface TraceWindow {
  start: number;
  end: number;
  total: number;
}

export type MarkerTone = "ok" | "warning" | "error" | "muted";

export interface SpanMarker {
  label: string;
  tone: MarkerTone;
  title: string;
}

export interface GateSummary {
  verdict: string | null;
  action: string | null;
  failedChecks: string | null;
  spanCount: number;
  corrective: boolean;
  // The action of the last gate span that asked for a correction, so a run
  // that retried and then passed still draws its corrective edge.
  correctiveAction: string | null;
}

export type PlanNodeKind = "cache" | "plan" | "agent" | "merge" | "gate";

export type PlanTone = "ok" | "warning" | "error" | "muted" | "neutral";

export interface PlanNode {
  id: string;
  kind: PlanNodeKind;
  title: string;
  detail: string;
  note: string | null;
  tone: PlanTone;
  // The span the node points at, so a click selects it in the detail pane.
  spanId: string | null;
  executed: boolean;
}

export interface PlanEdge {
  from: string;
  to: string;
  label: string;
  // A corrective edge goes backwards (gate to plan or gate to agents) and is
  // shown as a note instead of a layer, because the layout is a DAG.
  corrective: boolean;
}

export interface PlanGraph {
  nodes: PlanNode[];
  edges: PlanEdge[];
  layers: string[][];
  notes: string[];
  empty: boolean;
}

function text(value: unknown): string | null {
  if (typeof value === "string" && value !== "") {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return null;
}

export function flattenSpanTree(spans: TraceSpan[]): FlatSpan[] {
  const rows: FlatSpan[] = [];
  const walk = (nodes: TraceSpan[], depth: number) => {
    for (const node of nodes) {
      rows.push({ span: node, depth });
      walk(node.children, depth + 1);
    }
  };
  walk(spans, 0);
  return rows;
}

// The rows a view shows: a collapsed span hides its subtree in the tree and in
// the timeline, so both views share one expansion state.
export function visibleSpanRows(
  spans: TraceSpan[],
  expanded: ReadonlySet<string>,
): FlatSpan[] {
  const rows: FlatSpan[] = [];
  const walk = (nodes: TraceSpan[], depth: number) => {
    for (const node of nodes) {
      rows.push({ span: node, depth });
      if (node.children.length > 0 && expanded.has(node.span_id)) {
        walk(node.children, depth + 1);
      }
    }
  };
  walk(spans, 0);
  return rows;
}

export function expandableSpanIds(rows: FlatSpan[]): string[] {
  return rows.filter((row) => row.span.children.length > 0).map((row) => row.span.span_id);
}

export function ancestorsOf(rows: FlatSpan[], spanId: string): string[] {
  const byId = new Map(rows.map((row) => [row.span.span_id, row.span]));
  const ancestors: string[] = [];
  const seen = new Set<string>();
  let current = byId.get(spanId)?.parent_span_id ?? null;
  while (current !== null && !seen.has(current)) {
    seen.add(current);
    ancestors.push(current);
    current = byId.get(current)?.parent_span_id ?? null;
  }
  return ancestors;
}

export function traceWindow(rows: FlatSpan[]): TraceWindow {
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const row of rows) {
    const spanStart = Date.parse(row.span.start);
    const spanEnd = Date.parse(row.span.end);
    if (Number.isFinite(spanStart)) {
      start = Math.min(start, spanStart);
      end = Math.max(end, spanStart);
    }
    if (Number.isFinite(spanEnd)) {
      end = Math.max(end, spanEnd);
    }
  }
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return { start: 0, end: 0, total: 0 };
  }
  return { start, end: Math.max(end, start), total: Math.max(0, end - start) };
}

export function spanStartOffset(span: TraceSpan, window: TraceWindow): number {
  const started = Date.parse(span.start);
  if (!Number.isFinite(started)) {
    return 0;
  }
  return Math.max(0, started - window.start);
}

export function spanMarkers(span: TraceSpan): SpanMarker[] {
  const markers: SpanMarker[] = [];
  if (span.name === "gate") {
    const verdict = text(span.attributes["switchboard.gate.verdict"]);
    const action = text(span.attributes["switchboard.gate.action"]);
    const failed = text(span.attributes["switchboard.gate.failed_checks"]);
    markers.push({
      label: `gate.checked: ${verdict ?? "no verdict"}${action ? ` (${action})` : ""}`,
      tone: verdict === "pass" ? "ok" : verdict === "fail" ? "warning" : "muted",
      title: failed ? `failed checks: ${failed}` : "gate.checked from the gate span",
    });
  }
  if (span.name === "cache.lookup") {
    const hit = span.attributes["switchboard.cache.hit"];
    const similarity = text(span.attributes["switchboard.cache.similarity"]);
    markers.push({
      label: `cache: ${hit === true ? "hit" : hit === false ? "miss" : "checked"}`,
      tone: hit === true ? "ok" : "muted",
      title: similarity === null ? "cache.lookup span" : `similarity ${similarity}`,
    });
  }
  return markers;
}

export function gateSummary(rows: FlatSpan[]): GateSummary {
  const gateRows = rows.filter((row) => row.span.name === "gate");
  if (gateRows.length === 0) {
    return {
      verdict: null,
      action: null,
      failedChecks: null,
      spanCount: 0,
      corrective: false,
      correctiveAction: null,
    };
  }
  const last = gateRows[gateRows.length - 1].span;
  const correctiveSpan = [...gateRows]
    .reverse()
    .map((row) => row.span)
    .find((span) => {
      const action = text(span.attributes["switchboard.gate.action"]);
      return action === "retry" || action === "replan";
    });
  const correctiveAction =
    correctiveSpan === undefined
      ? null
      : text(correctiveSpan.attributes["switchboard.gate.action"]);
  return {
    verdict: text(last.attributes["switchboard.gate.verdict"]),
    action: text(last.attributes["switchboard.gate.action"]),
    failedChecks: text(last.attributes["switchboard.gate.failed_checks"]),
    spanCount: gateRows.length,
    corrective: correctiveAction !== null,
    correctiveAction,
  };
}

export function logTone(status: string): "ok" | "error" | "muted" {
  if (status === "error") {
    return "error";
  }
  if (status === "ok") {
    return "ok";
  }
  return "muted";
}

function planAgents(plan: string | null): string[] {
  if (plan === null || plan === "") {
    return [];
  }
  return plan
    .split("+")
    .map((agent) => agent.trim())
    .filter((agent) => agent !== "");
}

function addEdge(
  edges: PlanEdge[],
  seen: Set<string>,
  from: string,
  to: string,
  label: string,
  corrective = false,
): void {
  const key = `${from}->${to}:${label}`;
  if (seen.has(key)) {
    return;
  }
  seen.add(key);
  edges.push({ from, to, label, corrective });
}

// Layers come from the non-corrective edges: a node sits one level below its
// deepest predecessor. The node order of each layer keeps the graph stable.
function layoutLayers(nodes: PlanNode[], edges: PlanEdge[]): string[][] {
  const level = new Map<string, number>(nodes.map((node) => [node.id, 0]));
  const forward = edges.filter((edge) => !edge.corrective);
  for (let round = 0; round < nodes.length; round += 1) {
    let changed = false;
    for (const edge of forward) {
      const next = (level.get(edge.from) ?? 0) + 1;
      if (next > (level.get(edge.to) ?? 0)) {
        level.set(edge.to, next);
        changed = true;
      }
    }
    if (!changed) {
      break;
    }
  }
  const byLevel = new Map<number, string[]>();
  for (const node of nodes) {
    const value = level.get(node.id) ?? 0;
    const bucket = byLevel.get(value) ?? [];
    bucket.push(node.id);
    byLevel.set(value, bucket);
  }
  return [...byLevel.keys()]
    .sort((left, right) => left - right)
    .map((value) => byLevel.get(value) ?? []);
}

interface AgentGroup {
  name: string;
  spans: TraceSpan[];
}

function groupAgents(rows: FlatSpan[]): AgentGroup[] {
  const groups = new Map<string, TraceSpan[]>();
  for (const row of rows) {
    const span = row.span;
    if (!span.name.startsWith("agent.")) {
      continue;
    }
    const agent = text(span.attributes["switchboard.agent"]) ?? span.name.slice("agent.".length);
    const bucket = groups.get(agent) ?? [];
    bucket.push(span);
    groups.set(agent, bucket);
  }
  return [...groups.entries()].map(([name, spansForAgent]) => ({
    name,
    spans: spansForAgent,
  }));
}

/**
 * Build the plan graph of one run from its stored spans and the run record.
 *
 * LangGraph runs the plan before the agents (cache, supervisor, agents, merge,
 * gate); the agent loop records its plan after the tool loop (cache, agents,
 * plan, gate). The cache emits no span while it is a pass-through, so its node
 * says so instead of showing a made-up hit or miss. The merge step has no span
 * of its own, so it is only drawn when more than one agent span was stored and
 * only for an orchestrator whose graph has that step.
 */
export function buildPlanGraph(run: RunSummary | null, rows: FlatSpan[]): PlanGraph {
  if (rows.length === 0) {
    return {
      nodes: [],
      edges: [],
      layers: [],
      notes: [
        "No spans are stored for this run, so the executed plan graph cannot be drawn. The trace may have been exported elsewhere (OTLP) or lost.",
      ],
      empty: true,
    };
  }

  const nodes: PlanNode[] = [];
  const edges: PlanEdge[] = [];
  const notes: string[] = [];
  const seenEdges = new Set<string>();

  const isAgentLoop = run?.orchestrator === "agent_loop";
  const cacheSpans = rows.filter((row) => row.span.name === "cache.lookup").map((row) => row.span);
  const planSpans = rows.filter((row) => row.span.name === "supervisor.plan").map((row) => row.span);
  const gateSpans = rows.filter((row) => row.span.name === "gate").map((row) => row.span);
  const agents = groupAgents(rows);
  const runPlan = run?.plan ?? null;
  const recordedPlan =
    planSpans.length > 0
      ? text(planSpans[planSpans.length - 1].attributes["switchboard.plan"])
      : null;
  const gate = gateSummary(rows);

  const cacheNode: PlanNode = {
    id: "cache",
    kind: "cache",
    title: "cache",
    detail:
      cacheSpans.length > 0
        ? `executed ${cacheSpans.length} lookup(s)`
        : "not enabled in this build",
    note:
      cacheSpans.length > 0
        ? null
        : "The semantic cache is a pass-through until T2.18, so this trace has no cache span and no hit or miss is shown.",
    tone: cacheSpans.length > 0 ? "neutral" : "muted",
    spanId: cacheSpans.length > 0 ? cacheSpans[cacheSpans.length - 1].span_id : null,
    executed: cacheSpans.length > 0,
  };
  nodes.push(cacheNode);

  let planNodeId: string | null = null;
  if (planSpans.length > 0) {
    const last = planSpans[planSpans.length - 1];
    const reason = text(last.attributes["switchboard.plan.reason"]);
    const fallback = last.attributes["switchboard.plan.fallback"] === true;
    const replans = planSpans.length - 1;
    planNodeId = "plan";
    nodes.push({
      id: "plan",
      kind: "plan",
      title: isAgentLoop ? "plan (derived after the loop)" : "supervisor / plan",
      detail: `${recordedPlan ?? runPlan ?? "unknown plan"} (${planSpans.length} plan span(s)${replans > 0 ? `, ${replans} replan(s)` : ""})`,
      note: reason,
      tone: !isAgentLoop && fallback ? "warning" : "neutral",
      spanId: last.span_id,
      executed: true,
    });
    if (!isAgentLoop && fallback) {
      notes.push("The supervisor used its fallback plan.");
    }
  } else if (runPlan !== null) {
    planNodeId = "plan";
    nodes.push({
      id: "plan",
      kind: "plan",
      title: "supervisor / plan",
      detail: runPlan,
      note: "The run record has a plan but no supervisor.plan span is stored in this trace.",
      tone: "muted",
      spanId: null,
      executed: false,
    });
  } else {
    notes.push("Neither the run record nor the trace has a plan for this run.");
  }

  const agentNodes: PlanNode[] = agents.map((agent) => {
    const last = agent.spans[agent.spans.length - 1];
    const anyError = agent.spans.some((span) => span.status === "error");
    const anyPartial = agent.spans.some((span) => span.attributes["switchboard.partial"] === true);
    const citations = last.attributes["switchboard.citations"];
    const detailParts = [
      agent.spans.length === 1 ? "executed once" : `${agent.spans.length} spans (retry or replan)`,
      last.status === "error" ? "error" : null,
      anyPartial ? "partial" : null,
      citations !== undefined ? `${String(citations)} citation(s)` : null,
    ].filter((part): part is string => part !== null);
    return {
      id: `agent:${agent.name}`,
      kind: "agent",
      title: `agent.${agent.name}`,
      detail: detailParts.join(", "),
      note: null,
      tone: anyError ? "error" : anyPartial ? "warning" : "ok",
      spanId: last.span_id,
      executed: true,
    };
  });
  nodes.push(...agentNodes);

  const mergeNode = !isAgentLoop && agentNodes.length > 1;
  if (mergeNode) {
    nodes.push({
      id: "merge",
      kind: "merge",
      title: "merge",
      detail: `joins ${agentNodes.length} agent result(s)`,
      note: "The LangGraph merge step does not emit a span of its own; it is shown because the stored agent spans executed before the gate.",
      tone: "neutral",
      spanId: null,
      executed: true,
    });
  } else if (isAgentLoop && agentNodes.length > 1) {
    notes.push(
      "The agent loop has no merge step: its one loop combines the tool results before the gate.",
    );
  } else if (!isAgentLoop && agentNodes.length === 1) {
    notes.push(
      "The LangGraph merge step runs even for one agent but is not drawn here; the edge goes straight to the gate.",
    );
  }

  if (gateSpans.length > 0) {
    const last = gateSpans[gateSpans.length - 1];
    const verdict = text(last.attributes["switchboard.gate.verdict"]);
    const action = text(last.attributes["switchboard.gate.action"]);
    const failed = text(last.attributes["switchboard.gate.failed_checks"]);
    const gateNotes: string[] = [];
    if (failed !== null) {
      gateNotes.push(`failed checks: ${failed}`);
    }
    if (gate.corrective && gate.correctiveAction !== null) {
      gateNotes.push(`an earlier gate span asked for a ${gate.correctiveAction}`);
    }
    nodes.push({
      id: "gate",
      kind: "gate",
      title: "gate",
      detail: `${verdict ?? "no verdict"}${action !== null ? ` (action: ${action})` : ""}${
        gate.spanCount > 1 ? `, ${gate.spanCount} gate spans` : ""
      }`,
      note: gateNotes.length > 0 ? gateNotes.join(". ") : null,
      tone: verdict === "pass" ? "ok" : verdict === "fail" ? "warning" : "muted",
      spanId: last.span_id,
      executed: true,
    });
  } else {
    nodes.push({
      id: "gate",
      kind: "gate",
      title: "gate",
      detail: "no gate span in this trace",
      note: "The gate span was added in v1; a run recorded before it, or a lost span batch, has no verdict here.",
      tone: "muted",
      spanId: null,
      executed: false,
    });
  }

  // Edges follow the orchestrator's actual graph, not a generic flow.
  if (isAgentLoop) {
    // The loop does not call the cache in this build, so the cache node is not
    // connected to a step it never reached.
    notes.push(
      "The agent loop does not call the cache in this build, so the cache node has no edge.",
    );
    for (const agent of agentNodes) {
      if (planNodeId !== null) {
        addEdge(edges, seenEdges, agent.id, planNodeId, "plan recorded after the loop");
      }
    }
    if (planNodeId !== null) {
      addEdge(edges, seenEdges, planNodeId, "gate", "check");
    }
  } else {
    if (planNodeId !== null) {
      addEdge(edges, seenEdges, "cache", planNodeId, "pass-through");
      for (const agent of agentNodes) {
        addEdge(edges, seenEdges, planNodeId, agent.id, "dispatch");
      }
    } else {
      for (const agent of agentNodes) {
        addEdge(edges, seenEdges, "cache", agent.id, "pass-through");
      }
    }
    for (const agent of agentNodes) {
      addEdge(edges, seenEdges, agent.id, mergeNode ? "merge" : "gate", mergeNode ? "result" : "check");
    }
    if (mergeNode) {
      addEdge(edges, seenEdges, "merge", "gate", "merged answer");
    }
    if (agentNodes.length === 0 && planNodeId !== null) {
      addEdge(edges, seenEdges, planNodeId, "gate", "no agent span in this trace");
      notes.push(
        "No agent span is stored for this trace, so the graph goes from the plan straight to the gate.",
      );
    }
  }

  if (gate.corrective) {
    if (gate.correctiveAction === "retry" && agentNodes.length > 0) {
      for (const agent of agentNodes) {
        addEdge(edges, seenEdges, "gate", agent.id, "corrective: retry", true);
      }
    } else if (gate.correctiveAction === "replan" && planNodeId !== null) {
      addEdge(edges, seenEdges, "gate", planNodeId, "corrective: replan", true);
    }
  }

  const plannedAgents = planAgents(runPlan);
  const executedNames = new Set(agents.map((agent) => agent.name));
  const missing = plannedAgents.filter((agent) => !executedNames.has(agent));
  if (missing.length > 0) {
    notes.push(
      `The plan names ${missing.join(", ")} but no agent span is stored for it; the trace may be incomplete.`,
    );
  }
  if (run?.attempts !== undefined && run.attempts > 0) {
    notes.push(`The run record reports ${run.attempts} corrective attempt(s).`);
  }

  return {
    nodes,
    edges,
    layers: layoutLayers(nodes, edges),
    notes,
    empty: false,
  };
}

export function planLabel(run: RunSummary | null, rows: FlatSpan[]): string | null {
  if (run !== null && run.plan !== null) {
    return run.plan;
  }
  for (const row of rows) {
    if (row.span.name === "supervisor.plan") {
      const value = text(row.span.attributes["switchboard.plan"]);
      if (value !== null) {
        return value;
      }
    }
  }
  return null;
}
