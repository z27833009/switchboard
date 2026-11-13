/**
 * The two read-only review tools (design.md sections 7.1 to 7.4, T3.08).
 *
 * Every handler takes the tenant from the verified run token, checks the
 * tenant's allow list, passes the tenant's seller ids to the query, writes one
 * audit row and returns the standard `summary`, `rows`, `total_rows` and
 * `audit_id` payload. Nothing in the arguments can change the tenant scope.
 */

import { SpanKind, SpanStatusCode, trace } from "@opentelemetry/api";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { performance } from "node:perf_hooks";
import { z as zod } from "zod";

import type { TokenClaims } from "./auth.js";
import type { TenantSettings, ToolDefinition } from "./config.js";
import {
  type AuditInput,
  type Database,
  type Queryable,
  type ToolOutcome,
  hashArgs,
  insertAudit,
  runOrderReviews,
  runReviewSummary,
  withReadonlyTransaction,
} from "./db.js";

const tracer = trace.getTracer("switchboard.mcp_reviews");

export class PermissionDenied extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PermissionDenied";
  }
}

export interface ToolContext {
  readonly claims: TokenClaims;
  readonly tenant: TenantSettings;
  readonly manifest: ReadonlyMap<string, ToolDefinition>;
  readonly pool: Database;
}

function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

const isoDate = zod
  .string()
  .refine(isIsoDate, { message: "must be a calendar date in YYYY-MM-DD form" });

export const reviewSummarySchema = zod
  .strictObject({
    date_from: isoDate,
    date_to: isoDate,
    min_score: zod.number().int().min(1).max(5).optional(),
  })
  .refine((value) => value.date_from <= value.date_to, {
    message: "date_from must be on or before date_to",
  });

export const orderReviewsSchema = zod.strictObject({
  order_id: zod.string().min(1).max(200),
});

function callResult(outcome: ToolOutcome, auditId: string): CallToolResult {
  const payload = {
    summary: outcome.summary,
    rows: outcome.rows,
    total_rows: outcome.totalRows,
    audit_id: auditId,
  };
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    structuredContent: payload,
  };
}

function toolMeta(definition: ToolDefinition): Record<string, unknown> {
  return {
    switchboard: {
      version: definition.version,
      risk: definition.risk,
      tenant_scoped: definition.tenantScoped,
      sensitive_args: [...definition.sensitiveArgs],
    },
  };
}

/**
 * Verify the allow list before any database work, run the query in a read-only
 * transaction, then write the audit row. A denied call is audited and rejected;
 * a rejected token never gets here because the HTTP layer verifies it first.
 */
async function executeTool(
  context: ToolContext,
  wire: string,
  args: Record<string, unknown>,
  operation: (client: Queryable) => Promise<ToolOutcome>,
): Promise<CallToolResult> {
  const definition = context.manifest.get(wire);
  if (!definition) {
    throw new Error(`no manifest entry for ${wire}`);
  }
  const started = performance.now();
  return tracer.startActiveSpan(
    `execute_tool ${wire}`,
    {
      kind: SpanKind.SERVER,
      attributes: {
        "gen_ai.tool.name": wire,
        "switchboard.tool.version": definition.version,
        "switchboard.tool.args_hash": hashArgs(args),
        "switchboard.tenant_id": context.claims.tenantId,
        "switchboard.run_id": context.claims.runId,
      },
    },
    async (span): Promise<CallToolResult> => {
      try {
        const auditInput: AuditInput = {
          runId: context.claims.runId,
          tenantId: context.claims.tenantId,
          toolName: definition.name,
          toolVersion: definition.version,
          args,
          sensitiveArgs: definition.sensitiveArgs,
          status: "ok",
          durationMs: 0,
        };
        if (!context.tenant.allowedTools.has(wire)) {
          const durationMs = Math.round(performance.now() - started);
          let auditId: string | null = null;
          try {
            auditId = await insertAudit(context.pool, {
              ...auditInput,
              status: "denied",
              durationMs,
            });
          } catch (error) {
            // A denial must still be a denial when the audit table is unreachable.
            console.error(`audit write failed for denied ${definition.name}: ${String(error)}`);
          }
          span.setAttribute("switchboard.tool.status", "denied");
          if (auditId) {
            span.setAttribute("switchboard.tool.audit_id", auditId);
          }
          span.setStatus({ code: SpanStatusCode.ERROR, message: "tool not allowed" });
          throw new PermissionDenied(
            `tool ${wire} is not allowed for tenant ${context.claims.tenantId}`,
          );
        }

        const outcome = await withReadonlyTransaction(context.pool, operation);
        const durationMs = Math.round(performance.now() - started);
        const auditId = await insertAudit(context.pool, {
          ...auditInput,
          status: "ok",
          durationMs,
        });
        span.setAttribute("switchboard.tool.status", "ok");
        span.setAttribute("switchboard.tool.total_rows", outcome.totalRows);
        span.setAttribute("switchboard.tool.audit_id", auditId);
        span.setStatus({ code: SpanStatusCode.OK });
        return callResult(outcome, auditId);
      } catch (error) {
        if (!(error instanceof PermissionDenied)) {
          span.setAttribute("switchboard.tool.status", "error");
          span.recordException(error as Error);
          span.setStatus({
            code: SpanStatusCode.ERROR,
            message: (error as Error).message,
          });
        }
        throw error;
      } finally {
        span.end();
      }
    },
  );
}

/**
 * Register exactly the tools in the server's manifest.
 *
 * A manifest entry without a handler (or a handler without an entry) is a
 * startup configuration error, so the published tool list and the executable
 * code cannot drift apart.
 */
export function registerReviewTools(server: McpServer, context: ToolContext): void {
  const summary = context.manifest.get("review_summary@1");
  const reviews = context.manifest.get("order_reviews@1");
  if (!summary || !reviews) {
    throw new Error("the reviews manifest must declare review_summary@1 and order_reviews@1");
  }
  if (context.manifest.size !== 2) {
    throw new Error(
      `the reviews server serves two tools; the manifest declares ${context.manifest.size}`,
    );
  }

  server.registerTool(
    "review_summary@1",
    {
      description: summary.description,
      inputSchema: reviewSummarySchema,
      _meta: toolMeta(summary),
    },
    async (args): Promise<CallToolResult> =>
      executeTool(
        context,
        "review_summary@1",
        { ...args },
        (client) =>
          runReviewSummary(client, context.tenant.sellerIds, {
            dateFrom: args.date_from,
            dateTo: args.date_to,
            ...(args.min_score === undefined ? {} : { minScore: args.min_score }),
          }),
      ),
  );

  server.registerTool(
    "order_reviews@1",
    {
      description: reviews.description,
      inputSchema: orderReviewsSchema,
      _meta: toolMeta(reviews),
    },
    async (args): Promise<CallToolResult> =>
      executeTool(
        context,
        "order_reviews@1",
        { ...args },
        (client) =>
          runOrderReviews(client, context.tenant.sellerIds, { orderId: args.order_id }),
      ),
  );
}
