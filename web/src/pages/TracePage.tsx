import { useEffect, useState } from "react";

import { errorMessage, getJson, isAbortError } from "../api";
import type { SpanEvent, TraceResponse, TraceSpan } from "../api";
import { TENANTS } from "../config";
import { formatDuration, formatValue } from "../format";

interface SpanNodeProps {
  span: TraceSpan;
  depth: number;
  expanded: ReadonlySet<string>;
  selectedId: string | null;
  maxDuration: number;
  onToggle: (spanId: string) => void;
  onSelect: (spanId: string) => void;
}

function SpanNode({
  span,
  depth,
  expanded,
  selectedId,
  maxDuration,
  onToggle,
  onSelect,
}: SpanNodeProps) {
  const hasChildren = span.children.length > 0;
  const isExpanded = expanded.has(span.span_id);
  const barWidth =
    maxDuration > 0 ? Math.max(1, Math.round((span.duration_ms / maxDuration) * 100)) : 0;
  return (
    <>
      <div
        className={`span-row${selectedId === span.span_id ? " selected" : ""}`}
        style={{ paddingLeft: `${depth * 18 + 4}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            className="toggle"
            onClick={() => onToggle(span.span_id)}
            aria-expanded={isExpanded}
            aria-label={`${isExpanded ? "Collapse" : "Expand"} ${span.name}`}
          >
            {isExpanded ? "v" : ">"}
          </button>
        ) : (
          <span className="toggle-placeholder" aria-hidden="true" />
        )}
        <button type="button" className="span-select" onClick={() => onSelect(span.span_id)}>
          <span className="span-name">{span.name}</span>
          <span className="span-kind">{span.kind}</span>
          <span className={`span-status status-${span.status}`}>{span.status}</span>
          <span className="duration-bar" aria-hidden="true">
            <span style={{ width: `${barWidth}%` }} />
          </span>
          <span className="span-duration">{formatDuration(span.duration_ms)}</span>
        </button>
      </div>
      {hasChildren && isExpanded
        ? span.children.map((child) => (
            <SpanNode
              key={child.span_id}
              span={child}
              depth={depth + 1}
              expanded={expanded}
              selectedId={selectedId}
              maxDuration={maxDuration}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))
        : null}
    </>
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
  return (
    <div>
      <h3>{span.name}</h3>
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

function flatten(spans: TraceSpan[]): TraceSpan[] {
  const result: TraceSpan[] = [];
  const walk = (nodes: TraceSpan[]) => {
    for (const node of nodes) {
      result.push(node);
      walk(node.children);
    }
  };
  walk(spans);
  return result;
}

function ancestorsOf(flat: TraceSpan[], spanId: string): string[] {
  const byId = new Map(flat.map((span) => [span.span_id, span]));
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

export interface TracePageProps {
  runId: string;
  tenant: string | null;
  spanId: string | null;
  auditId: string | null;
}

export function TracePage({ runId, tenant, spanId, auditId }: TracePageProps) {
  const tenantId = tenant ?? TENANTS[0];
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getJson<TraceResponse>(
      `/runs/${encodeURIComponent(runId)}/trace?tenant_id=${encodeURIComponent(tenantId)}`,
      controller.signal,
    )
      .then((data) => {
        setTrace(data);
        setExpanded(
          new Set(
            flatten(data.spans)
              .filter((span) => span.children.length > 0)
              .map((span) => span.span_id),
          ),
        );
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
  }, [runId, tenantId]);

  useEffect(() => {
    if (trace === null) {
      return;
    }
    const flat = flatten(trace.spans);
    let match: TraceSpan | null = null;
    if (spanId !== null) {
      match = flat.find((span) => span.span_id === spanId) ?? null;
      if (match === null) {
        setSelectionNotice(`No span with span id ${spanId} is stored for this run.`);
      } else {
        setSelectionNotice(null);
      }
    } else if (auditId !== null) {
      match =
        flat.find((span) => {
          const value = span.attributes["switchboard.tool.audit_id"];
          return typeof value === "string" && value === auditId;
        }) ?? null;
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
      const ancestors = ancestorsOf(flat, match.span_id);
      setExpanded((current) => {
        const next = new Set(current);
        for (const id of ancestors) {
          next.add(id);
        }
        return next;
      });
    }
  }, [trace, spanId, auditId]);

  const flat = trace === null ? [] : flatten(trace.spans);
  const selected = flat.find((span) => span.span_id === selectedId) ?? null;
  const maxDuration = flat.reduce((max, span) => Math.max(max, span.duration_ms), 0);

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

  return (
    <div className="page">
      <section className="panel">
        <div className="panel-header">
          <h2>Trace</h2>
          {trace !== null ? (
            <span className="muted">
              {trace.span_count} span(s), trace id {trace.trace_id ?? "not shared"}
            </span>
          ) : null}
        </div>
        <p className="meta">
          run <code>{runId}</code>
        </p>
        {loading ? <p className="muted">Loading trace...</p> : null}
        {error !== null ? (
          <div className="error-box">
            <strong>Could not load the trace</strong>
            <p>{error}</p>
          </div>
        ) : null}
        {trace !== null && trace.incomplete ? (
          <div className="warning-box">
            <strong>Incomplete trace.</strong> The API reported that span data is missing for this run,
            because the flush timed out or a span batch was lost. The tree below contains only the spans that
            were stored; it is not a complete record of the run.
          </div>
        ) : null}
        {selectionNotice !== null ? <div className="warning-box">{selectionNotice}</div> : null}
        {trace !== null ? (
          <div className="trace-layout">
            <div className="trace-tree">
              {trace.spans.length === 0 ? (
                <p className="muted">The API returned no spans for this run.</p>
              ) : (
                trace.spans.map((span) => (
                  <SpanNode
                    key={span.span_id}
                    span={span}
                    depth={0}
                    expanded={expanded}
                    selectedId={selectedId}
                    maxDuration={maxDuration}
                    onToggle={handleToggle}
                    onSelect={setSelectedId}
                  />
                ))
              )}
            </div>
            <aside className="trace-detail">
              <SpanDetail span={selected} />
            </aside>
          </div>
        ) : null}
        <p className="links">
          <a href={`#/runs/${encodeURIComponent(runId)}?tenant=${encodeURIComponent(tenantId)}`}>
            Open run
          </a>
          <a href="#/runs">Back to runs</a>
        </p>
      </section>
    </div>
  );
}
