import { useEffect, useState } from "react";

import { errorMessage, getJson, isAbortError, postJson } from "../api";
import type { ChatResponse, RunSummary, SettingsResponse, SpanEvent, TraceResponse, TraceSpan } from "../api";
import { ApprovalCard } from "../components/Approval";
import { StatusBadge } from "../components/Badges";
import { ChevronDownIcon } from "../components/Icons";
import { TENANTS } from "../config";
import { formatDateTime, formatDuration, formatTokens, formatValue } from "../format";
import { runHref, tabHref, traceHref } from "../routes";
import type { TraceView } from "../routes";
import {
  ancestorsOf,
  buildPlanGraph,
  expandableSpanIds,
  flattenSpanTree,
  gateSummary,
  logTone,
  planLabel,
  spanMarkers,
  spanStartOffset,
  traceWindow,
  visibleSpanRows,
} from "../trace";
import type { FlatSpan, PlanEdge, PlanGraph, PlanNode, SpanMarker } from "../trace";

function MarkerChips({ markers }: { markers: SpanMarker[] }) {
  if (markers.length === 0) {
    return null;
  }
  return (
    <>
      {markers.map((marker) => (
        <span key={marker.label} className={`marker marker-${marker.tone}`} title={marker.title}>
          {marker.label}
        </span>
      ))}
    </>
  );
}

function SpanToggle({
  span,
  expanded,
  onToggle,
}: {
  span: TraceSpan;
  expanded: ReadonlySet<string>;
  onToggle: (spanId: string) => void;
}) {
  if (span.children.length === 0) {
    return <span className="toggle-placeholder" aria-hidden="true" />;
  }
  const isExpanded = expanded.has(span.span_id);
  return (
    <button
      type="button"
      className={`toggle${isExpanded ? " is-open" : ""}`}
      onClick={() => onToggle(span.span_id)}
      aria-expanded={isExpanded}
      aria-label={`${isExpanded ? "Collapse" : "Expand"} ${span.name}`}
    >
      <ChevronDownIcon className="toggle-icon" />
    </button>
  );
}

function TreeGuides({ row }: { row: FlatSpan }) {
  if (row.depth === 0) {
    return null;
  }
  // YouTube-style thread: the line continues below the elbow only while the
  // current node still has a later sibling at this depth.
  const continueLine = !row.isLast;
  return (
    <span className="tree-guides" aria-hidden="true">
      {row.guides.slice(0, -1).map((hasLine, index) => (
        <span key={index} className={`tree-guide${hasLine ? " has-line" : ""}`} />
      ))}
      <span className={`tree-branch${continueLine ? " continue" : ""}`} />
    </span>
  );
}

function TreeRow({
  row,
  expanded,
  selectedId,
  maxDuration,
  onToggle,
  onSelect,
}: {
  row: FlatSpan;
  expanded: ReadonlySet<string>;
  selectedId: string | null;
  maxDuration: number;
  onToggle: (spanId: string) => void;
  onSelect: (spanId: string) => void;
}) {
  const { span } = row;
  const barWidth =
    maxDuration > 0 ? Math.max(1, Math.round((span.duration_ms / maxDuration) * 100)) : 0;
  const markers = spanMarkers(span);
  return (
    <div className={`span-row${selectedId === span.span_id ? " selected" : ""}`}>
      <TreeGuides row={row} />
      <SpanToggle span={span} expanded={expanded} onToggle={onToggle} />
      <button type="button" className="span-select" onClick={() => onSelect(span.span_id)}>
        <span className="span-name">{span.name}</span>
        <span className="span-kind">{span.kind}</span>
        <span className={`span-status status-${span.status}`}>{span.status}</span>
        <MarkerChips markers={markers} />
        <span className="duration-bar" aria-hidden="true">
          <span style={{ width: `${barWidth}%` }} />
        </span>
        <span className="span-duration">{formatDuration(span.duration_ms)}</span>
      </button>
    </div>
  );
}

function TimelineRow({
  row,
  window,
  expanded,
  selectedId,
  onToggle,
  onSelect,
}: {
  row: FlatSpan;
  window: { start: number; total: number };
  expanded: ReadonlySet<string>;
  selectedId: string | null;
  onToggle: (spanId: string) => void;
  onSelect: (spanId: string) => void;
}) {
  const { span, depth } = row;
  const offset = spanStartOffset(span, {
    start: window.start,
    end: window.start + window.total,
    total: window.total,
  });
  const total = Math.max(1, window.total);
  const leftPct = Math.min(99.5, (offset / total) * 100);
  const widthPct = Math.max(0.5, (span.duration_ms / total) * 100);
  const markers = spanMarkers(span);
  const tone = logTone(span.status);
  return (
    <div
      className={`timeline-row${selectedId === span.span_id ? " selected" : ""}`}
    >
      <div className="timeline-label" style={{ paddingLeft: `${depth * 14 + 2}px` }}>
        <SpanToggle span={span} expanded={expanded} onToggle={onToggle} />
        <button
          type="button"
          className="timeline-select"
          onClick={() => onSelect(span.span_id)}
          title={`${span.name} (${span.kind}, ${span.status})`}
        >
          <span className="span-name">{span.name}</span>
          <span className="span-kind">{span.kind}</span>
          <span className={`span-status status-${span.status}`}>{span.status}</span>
          <MarkerChips markers={markers} />
        </button>
      </div>
      <div className="timeline-track">
        <button
          type="button"
          className={`timeline-bar bar-${tone}`}
          style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
          onClick={() => onSelect(span.span_id)}
          aria-label={`Select ${span.name}, ${formatDuration(span.duration_ms)}`}
          title={`${span.name}\nstart +${formatDuration(offset)}\nduration ${formatDuration(span.duration_ms)}\nstatus ${span.status}`}
        />
      </div>
      <span className="timeline-offset">+{formatDuration(offset)}</span>
      <span className="timeline-duration">{formatDuration(span.duration_ms)}</span>
    </div>
  );
}

function parseToolResult(event: SpanEvent): { parsed: unknown; truncated: boolean } | null {
  const text = event.attributes["switchboard.tool.result"];
  if (typeof text !== "string") {
    return null;
  }
  const truncated = event.attributes["switchboard.tool.result.truncated"] === true;
  try {
    return { parsed: JSON.parse(text), truncated };
  } catch {
    return null;
  }
}

function SpanEventView({ event }: { event: SpanEvent }) {
  const toolResult = parseToolResult(event);
  const resultText = event.attributes["switchboard.tool.result"];
  return (
    <div className="event">
      <div className="event-header">
        <strong>{event.name}</strong> <span className="muted">{event.time}</span>
      </div>
      <table className="attributes">
        <tbody>
          {Object.entries(event.attributes).map(([key, value]) => (
            <tr key={key}>
              <th>{key}</th>
              <td>
                <pre>{formatValue(value)}</pre>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <details open={event.name === "tool.result"}>
        <summary>Full event JSON</summary>
        <pre>{JSON.stringify(event, null, 2)}</pre>
      </details>
      {toolResult !== null ? (
        <details open>
          <summary>
            switchboard.tool.result as parsed JSON
            {toolResult.truncated ? " (truncated by the exporter, not by this page)" : ""}
          </summary>
          <pre>{JSON.stringify(toolResult.parsed, null, 2)}</pre>
        </details>
      ) : null}
      {typeof resultText === "string" && toolResult === null ? (
        <p className="warning-text">
          The tool result attribute is not valid JSON. The full event JSON above contains it unchanged.
        </p>
      ) : null}
      {toolResult !== null && toolResult.truncated ? (
        <p className="warning-text">
          The API exporter capped this tool result at 64 KB and set
          <code> switchboard.tool.result.truncated</code> to true. Everything the API stored is shown here.
        </p>
      ) : null}
    </div>
  );
}

function SpanDetail({ span }: { span: TraceSpan | null }) {
  if (span === null) {
    return <p className="muted">Select a span to see its attributes and events.</p>;
  }
  const attributeEntries = Object.entries(span.attributes);
  const markers = spanMarkers(span);
  return (
    <div>
      <h3>{span.name}</h3>
      {markers.length > 0 ? (
        <p className="marker-line">
          <MarkerChips markers={markers} />
        </p>
      ) : null}
      <dl className="facts">
        <div>
          <dt>span id</dt>
          <dd>
            <code>{span.span_id}</code>
          </dd>
        </div>
        <div>
          <dt>parent</dt>
          <dd>{span.parent_span_id === null ? "none (root)" : <code>{span.parent_span_id}</code>}</dd>
        </div>
        <div>
          <dt>trace id</dt>
          <dd>
            <code>{span.trace_id}</code>
          </dd>
        </div>
        <div>
          <dt>kind</dt>
          <dd>{span.kind}</dd>
        </div>
        <div>
          <dt>status</dt>
          <dd>{span.status}</dd>
        </div>
        <div>
          <dt>start</dt>
          <dd>
            <code>{span.start}</code>
          </dd>
        </div>
        <div>
          <dt>end</dt>
          <dd>
            <code>{span.end}</code>
          </dd>
        </div>
        <div>
          <dt>duration</dt>
          <dd>{formatDuration(span.duration_ms)}</dd>
        </div>
      </dl>

      <h4>Attributes ({attributeEntries.length})</h4>
      {attributeEntries.length === 0 ? (
        <p className="muted">This span has no attributes.</p>
      ) : (
        <table className="attributes">
          <tbody>
            {attributeEntries.map(([key, value]) => (
              <tr key={key}>
                <th>{key}</th>
                <td>
                  <pre>{formatValue(value)}</pre>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Events ({span.events.length})</h4>
      {span.events.length === 0 ? (
        <p className="muted">This span has no events.</p>
      ) : (
        span.events.map((event, index) => (
          <SpanEventView key={`${event.name}-${event.time}-${index}`} event={event} />
        ))
      )}
    </div>
  );
}

function PlanNodeCard({
  node,
  selectedId,
  onSelect,
}: {
  node: PlanNode;
  selectedId: string | null;
  onSelect: (spanId: string) => void;
}) {
  const className = `plan-node plan-node-${node.tone}${
    node.executed ? "" : " plan-node-skipped"
  }${node.spanId !== null && node.spanId === selectedId ? " selected" : ""}`;
  const body = (
    <>
      <span className="plan-node-kind">{node.kind}</span>
      <strong className="plan-node-title">{node.title}</strong>
      <span className="plan-node-detail">{node.detail}</span>
      {node.note !== null ? <span className="plan-node-note">{node.note}</span> : null}
      {node.spanId !== null ? (
        <span className="plan-node-span">span {node.spanId.slice(0, 12)}</span>
      ) : (
        <span className="plan-node-span">no span</span>
      )}
    </>
  );
  if (node.spanId === null) {
    return <div className={className}>{body}</div>;
  }
  const spanId = node.spanId;
  return (
    <button
      type="button"
      className={`${className} plan-node-select`}
      onClick={() => onSelect(spanId)}
    >
      {body}
    </button>
  );
}

function edgeLabel(edges: PlanEdge[]): string {
  const labels = [...new Set(edges.map((edge) => edge.label))];
  return labels.join(", ");
}

function PlanGraphView({
  graph,
  rows,
  selectedId,
  onSelect,
}: {
  graph: PlanGraph;
  rows: FlatSpan[];
  selectedId: string | null;
  onSelect: (spanId: string) => void;
}) {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  if (graph.empty) {
    return (
      <div className="plan-graph">
        {graph.notes.map((note) => (
          <p key={note} className="warning-text">
            {note}
          </p>
        ))}
      </div>
    );
  }
  const corrective = graph.edges.filter((edge) => edge.corrective);
  return (
    <div className="plan-graph">
      <p className="muted">
        Built from the run record and the stored spans. Every node is an executed span or a
        graph step this run recorded; a node without a span says so.
      </p>
      <div className="plan-flow" role="list" aria-label="Plan flow, left to right">
        {graph.layers.map((layer, index) => {
          const from = index === 0 ? [] : graph.layers[index - 1];
          const layerEdges = graph.edges.filter(
            (edge) => !edge.corrective && from.includes(edge.from) && layer.includes(edge.to),
          );
          return (
            <div key={`layer-${index}`} className="plan-flow-step">
              {index > 0 ? (
                <div className="plan-flow-edge" aria-hidden="true">
                  <span className="plan-flow-label">{edgeLabel(layerEdges)}</span>
                  <span className="plan-flow-arrow">›</span>
                </div>
              ) : null}
              <div className="plan-flow-column" role="listitem">
                {layer.map((id) => {
                  const node = byId.get(id);
                  if (node === undefined) {
                    return null;
                  }
                  return (
                    <PlanNodeCard
                      key={node.id}
                      node={node}
                      selectedId={selectedId}
                      onSelect={onSelect}
                    />
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      {corrective.length > 0 ? (
        <div className="plan-corrective">
          <strong>Corrective edges</strong>
          <ul>
            {corrective.map((edge) => (
              <li key={`${edge.from}-${edge.to}-${edge.label}`}>
                <code>{edge.from}</code> back to <code>{edge.to}</code>: {edge.label}
                {edge.to.startsWith("agent:") && byId.has(edge.to)
                  ? ` (${byId.get(edge.to)?.title ?? edge.to})`
                  : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {graph.notes.length > 0 ? (
        <ul className="plan-notes">
          {graph.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      <p className="muted">
        Click a node with a span to select it in the attribute pane below. {rows.length} span(s)
        feed this view.
      </p>
    </div>
  );
}

function planLabelOf(result: ChatResponse): string {
  return result.plan === null ? "no plan" : result.plan.agents.join("+");
}

function ReplayPanel({ run, tenantId }: { run: RunSummary; tenantId: string }) {
  const [options, setOptions] = useState<string[] | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [choice, setChoice] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ payload: ChatResponse; orchestrator: string } | null>(
    null,
  );

  useEffect(() => {
    const controller = new AbortController();
    setOptions(null);
    setOptionsError(null);
    getJson<SettingsResponse>(
      `/settings?tenant_id=${encodeURIComponent(tenantId)}`,
      controller.signal,
    )
      .then((payload) => {
        if (!controller.signal.aborted) {
          setOptions(payload.available_orchestrators);
        }
      })
      .catch((caught) => {
        if (!isAbortError(caught)) {
          setOptions([]);
          setOptionsError(errorMessage(caught));
        }
      });
    return () => controller.abort();
  }, [tenantId]);

  // Default to an orchestrator other than the recorded one, since comparing
  // two control flows is the point of the control; a user choice wins.
  const defaultChoice = options?.find((name) => name !== run.orchestrator) ?? options?.[0] ?? "";
  const selected = options?.includes(choice) === true ? choice : defaultChoice;
  const canReplay = !running && options !== null && selected !== "";

  async function startReplay() {
    if (!canReplay) {
      return;
    }
    const orchestrator = selected;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const payload = await postJson<ChatResponse>("/chat", {
        query: run.query,
        tenant_id: tenantId,
        orchestrator,
      });
      setResult({ payload, orchestrator });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="surface">
      <div className="surface-head">
        <h2 className="surface-title">Replay</h2>
      </div>
      <p className="muted small">
        Run the recorded query again on another registered orchestrator. This starts a new run on
        the tenant&apos;s configured provider and leaves the run above untouched. A replay does not
        carry the original session history.
      </p>
      {options === null ? <p className="muted">Loading orchestrators...</p> : null}
      {optionsError !== null ? (
        <div className="error-box" role="alert">
          <strong>Could not load the replay targets</strong>
          <p>{optionsError}</p>
        </div>
      ) : null}
      {options !== null && options.length === 0 && optionsError === null ? (
        <p className="muted">This process reports no available orchestrator for the tenant.</p>
      ) : null}
      {options !== null && options.length > 0 ? (
        <div className="replay-controls">
          <label className="field field-inline">
            <span className="field-label">Orchestrator</span>
            <select
              value={selected}
              onChange={(event) => setChoice(event.target.value)}
              disabled={running}
            >
              {options.map((name) => (
                <option key={name} value={name}>
                  {name}
                  {name === run.orchestrator ? " (recorded)" : ""}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="button" onClick={startReplay} disabled={!canReplay}>
            {running ? "Replaying..." : "Replay"}
          </button>
        </div>
      ) : null}
      {running ? (
        <p className="muted" role="status">
          A new run is running. It can take as long as the original.
        </p>
      ) : null}
      {error !== null ? (
        <div className="error-box" role="alert">
          <strong>Replay failed</strong>
          <p>{error}</p>
        </div>
      ) : null}
      {result !== null ? (
        <div className="replay-result" role="status">
          <p>
            <strong>Replay finished</strong> <StatusBadge status={result.payload.status} />{" "}
            <a href={traceHref(result.payload.run_id, tenantId, null)}>Open trace</a>
            <span className="dot-separator" aria-hidden="true">
              ·
            </span>
            <a href={runHref(result.payload.run_id, tenantId, null)}>Run detail</a>
          </p>
          <p className="muted small">
            run <code>{result.payload.run_id}</code> on <code>{result.orchestrator}</code> · plan{" "}
            {planLabelOf(result.payload)}
            {result.payload.trace_incomplete ? " · trace incomplete" : ""}
          </p>
        </div>
      ) : null}
    </section>
  );
}

export interface TracePageProps {
  runId: string;
  tenant: string | null;
  sessionId: string | null;
  spanId: string | null;
  auditId: string | null;
  view: TraceView | null;
}

export function TracePage({
  runId,
  tenant,
  sessionId,
  spanId,
  auditId,
  view,
}: TracePageProps) {
  const tenantId = tenant ?? TENANTS[0];
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [run, setRun] = useState<RunSummary | null>(null);
  const activeSession = sessionId ?? run?.session_id ?? null;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<TraceView>(view ?? "tree");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const query = `tenant_id=${encodeURIComponent(tenantId)}`;
    Promise.all([
      getJson<TraceResponse>(
        `/runs/${encodeURIComponent(runId)}/trace?${query}`,
        controller.signal,
      ),
      getJson<RunSummary>(`/runs/${encodeURIComponent(runId)}?${query}`, controller.signal),
    ])
      .then(([traceData, runData]) => {
        setTrace(traceData);
        setRun(runData);
        setExpanded(new Set(expandableSpanIds(flattenSpanTree(traceData.spans))));
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
  }, [runId, tenantId, reloadKey]);

  function handleDecided(result: ChatResponse) {
    // The decision finished the run: reload both the run and its trace, because
    // the resumed graph appended spans (T3.01).
    setRun((current) =>
      current === null
        ? current
        : {
            ...current,
            status: result.status,
            answer: result.answer,
            citations: result.citations,
            errors: result.errors,
            pending_approval: null,
            ended_at: result.ended_at,
          },
    );
    setReloadKey((value) => value + 1);
  }

  const allRows = trace === null ? [] : flattenSpanTree(trace.spans);

  function selectSpan(spanIdToSelect: string) {
    setSelectedId(spanIdToSelect);
    setSelectionNotice(null);
    setExpanded((current) => {
      const next = new Set(current);
      for (const id of ancestorsOf(allRows, spanIdToSelect)) {
        next.add(id);
      }
      return next;
    });
  }

  useEffect(() => {
    if (trace === null) {
      return;
    }
    const rows = flattenSpanTree(trace.spans);
    let match: TraceSpan | null = null;
    if (spanId !== null) {
      match = rows.find((row) => row.span.span_id === spanId)?.span ?? null;
      if (match === null) {
        setSelectionNotice(`No span with span id ${spanId} is stored for this run.`);
      } else {
        setSelectionNotice(null);
      }
    } else if (auditId !== null) {
      match =
        rows.find((row) => {
          const value = row.span.attributes["switchboard.tool.audit_id"];
          return typeof value === "string" && value === auditId;
        })?.span ?? null;
      if (match === null) {
        setSelectionNotice(`No tool span with audit id ${auditId} is stored for this run.`);
      } else {
        setSelectionNotice(null);
      }
    } else {
      setSelectionNotice(null);
    }
    setSelectedId(match?.span_id ?? null);
    if (match !== null) {
      const ancestors = ancestorsOf(rows, match.span_id);
      setExpanded((current) => {
        const next = new Set(current);
        for (const id of ancestors) {
          next.add(id);
        }
        return next;
      });
    }
  }, [trace, spanId, auditId]);

  const selected = allRows.find((row) => row.span.span_id === selectedId)?.span ?? null;
  const maxDuration = allRows.reduce((max, row) => Math.max(max, row.span.duration_ms), 0);
  const window = traceWindow(allRows);
  const visibleRows = trace === null ? [] : visibleSpanRows(trace.spans, expanded);
  const graph = buildPlanGraph(run, allRows);
  const gate = gateSummary(allRows);
  const recordedPlan = planLabel(run, allRows);

  function handleToggle(spanIdToToggle: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(spanIdToToggle)) {
        next.delete(spanIdToToggle);
      } else {
        next.add(spanIdToToggle);
      }
      return next;
    });
  }

  function expandAll() {
    setExpanded(new Set(expandableSpanIds(allRows)));
  }

  function collapseAll() {
    setExpanded(new Set());
  }

  return (
    <div className="page page-trace">
      <p className="back-link">
        <a href={tabHref("traces", tenantId, activeSession)}>Back to traces</a>
        <span className="dot-separator" aria-hidden="true">
          ·
        </span>
        <a href={runHref(runId, tenantId, activeSession)}>Run detail</a>
      </p>
      <p className="turn-meta">
        run <code>{runId}</code> · tenant <code>{tenantId}</code>
        {trace !== null ? (
          <>
            {" "}
            · {trace.span_count} span(s) · trace id <code>{trace.trace_id ?? "not shared"}</code>
          </>
        ) : null}
      </p>
      {run !== null ? (
        <dl className="trace-summary">
          <div className="kv">
            <dt>status</dt>
            <dd>
              <StatusBadge status={run.status} />
            </dd>
          </div>
          <div className="kv">
            <dt>plan</dt>
            <dd>{recordedPlan ?? "-"}</dd>
          </div>
          <div className="kv">
            <dt>orchestrator</dt>
            <dd>
              <code>{run.orchestrator}</code>
            </dd>
          </div>
          <div className="kv">
            <dt>gate</dt>
            <dd>
              {gate.verdict === null ? (
                <span className="muted">no gate span</span>
              ) : (
                <span className={`marker marker-${gate.verdict === "pass" ? "ok" : "warning"}`}>
                  {gate.verdict}
                  {gate.action !== null ? ` (${gate.action})` : ""}
                  {gate.spanCount > 1 ? `, ${gate.spanCount} checks` : ""}
                </span>
              )}
              {gate.failedChecks !== null ? (
                <span className="warning-text"> failed: {gate.failedChecks}</span>
              ) : null}
              {gate.corrective && gate.correctiveAction !== null ? (
                <span className="marker marker-warning">
                  corrective: {gate.correctiveAction} ({gate.spanCount} gate check(s))
                </span>
              ) : null}
            </dd>
          </div>
          <div className="kv">
            <dt>attempts</dt>
            <dd>{run.attempts}</dd>
          </div>
          <div className="kv">
            <dt>tokens</dt>
            <dd>
              {formatTokens(run.input_tokens)} in / {formatTokens(run.output_tokens)} out
            </dd>
          </div>
          <div className="kv">
            <dt>cost</dt>
            <dd>{run.cost_usd !== null ? `$${run.cost_usd.toFixed(4)}` : "no API cost recorded"}</dd>
          </div>
          <div className="kv">
            <dt>started</dt>
            <dd>{formatDateTime(run.started_at)}</dd>
          </div>
          <div className="kv">
            <dt>ended</dt>
            <dd>{formatDateTime(run.ended_at)}</dd>
          </div>
        </dl>
      ) : null}

      {run !== null ? <ReplayPanel run={run} tenantId={tenantId} /> : null}

      {run !== null && run.status === "waiting_approval" && run.pending_approval !== null ? (
        <ApprovalCard
          runId={run.run_id}
          tenantId={run.tenant_id}
          pending={run.pending_approval}
          onDecided={handleDecided}
        />
      ) : null}

      {loading ? <p className="muted">Loading trace...</p> : null}
      {error !== null ? (
        <div className="error-box" role="alert">
          <strong>Could not load the trace</strong>
          <p>{error}</p>
        </div>
      ) : null}
      {trace !== null && trace.incomplete ? (
        <div className="warning-box" role="status">
          <strong>Incomplete trace.</strong> The API reported that span data is missing for this
          run, because the flush timed out or a span batch was lost. The views below contain only
          the spans that were stored; they are not a complete record of the run.
        </div>
      ) : null}
      {selectionNotice !== null ? (
        <div className="warning-box" role="status">
          {selectionNotice}
        </div>
      ) : null}

      {trace !== null ? (
        <>
          <section className="surface">
            <h2 className="surface-title">Plan graph</h2>
            <PlanGraphView
              graph={graph}
              rows={allRows}
              selectedId={selectedId}
              onSelect={selectSpan}
            />
          </section>

          <section className="surface">
            <div className="surface-head trace-toolbar">
              <h2 className="surface-title">Spans</h2>
              <div className="view-switch" role="group" aria-label="Span view">
                <button
                  type="button"
                  aria-pressed={activeView === "tree"}
                  className={activeView === "tree" ? "active" : undefined}
                  onClick={() => setActiveView("tree")}
                >
                  Tree
                </button>
                <button
                  type="button"
                  aria-pressed={activeView === "timeline"}
                  className={activeView === "timeline" ? "active" : undefined}
                  onClick={() => setActiveView("timeline")}
                >
                  Timeline
                </button>
                <button type="button" onClick={expandAll}>
                  Expand all
                </button>
                <button type="button" onClick={collapseAll}>
                  Collapse all
                </button>
              </div>
            </div>

            <div
              className={`trace-layout${activeView === "timeline" ? " timeline-layout" : ""}`}
            >
              <div className={activeView === "tree" ? "trace-tree" : "trace-timeline"}>
                {allRows.length === 0 ? (
                  <p className="muted">
                    The API returned no spans for this run, so there is nothing to draw in either
                    view. If <code>tracing.exporter</code> is <code>otlp</code>, the spans are in
                    the collector instead of the platform trace table.
                  </p>
                ) : activeView === "tree" ? (
                  visibleRows.map((row) => (
                    <TreeRow
                      key={row.span.span_id}
                      row={row}
                      expanded={expanded}
                      selectedId={selectedId}
                      maxDuration={maxDuration}
                      onToggle={handleToggle}
                      onSelect={selectSpan}
                    />
                  ))
                ) : (
                  <>
                    <div className="timeline-axis" aria-hidden="true">
                      <span className="timeline-axis-label">span</span>
                      <div className="timeline-track">
                        {[0, 0.5, 1].map((fraction) => (
                          <span
                            key={fraction}
                            className="timeline-tick"
                            style={{
                              left: `${fraction * 100}%`,
                              transform:
                                fraction === 0
                                  ? "none"
                                  : fraction === 1
                                    ? "translateX(-100%)"
                                    : "translateX(-50%)",
                            }}
                          >
                            +{formatDuration(window.total * fraction)}
                          </span>
                        ))}
                      </div>
                      <span className="timeline-offset">start</span>
                      <span className="timeline-duration">duration</span>
                    </div>
                    {visibleRows.map((row) => (
                      <TimelineRow
                        key={row.span.span_id}
                        row={row}
                        window={window}
                        expanded={expanded}
                        selectedId={selectedId}
                        onToggle={handleToggle}
                        onSelect={selectSpan}
                      />
                    ))}
                  </>
                )}
                {visibleRows.length < allRows.length ? (
                  <p className="muted timeline-hint">
                    {allRows.length - visibleRows.length} span(s) are hidden by a collapsed parent.
                  </p>
                ) : null}
              </div>
              <aside className="trace-detail">
                <SpanDetail span={selected} />
              </aside>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
