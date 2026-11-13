/**
 * Streamable HTTP entry point for the reviews MCP server (T3.08).
 *
 * `/mcp` requires the HS256 run token on every request and takes the tenant
 * only from it (ADR 0014). `/health` is unauthenticated so Docker can probe it.
 * The transport is stateless, like the Olist server: one MCP server instance is
 * built per request from the verified token.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { type IncomingMessage, type ServerResponse, createServer } from "node:http";
import type { Server } from "node:http";

import { type TokenClaims, bearerToken, verifyRunToken } from "./auth.js";
import type { TenantSettings, ToolDefinition } from "./config.js";
import type { Database } from "./db.js";
import { registerReviewTools } from "./tools.js";

const MAX_BODY_BYTES = 256 * 1024;

export interface ReviewsServerDeps {
  readonly secret: string;
  readonly manifest: ReadonlyMap<string, ToolDefinition>;
  readonly tenants: ReadonlyMap<string, TenantSettings>;
  readonly pool: Database;
}

class BodyError extends Error {}

function respondJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as string);
    size += buffer.length;
    if (size > MAX_BODY_BYTES) {
      throw new BodyError("request body is too large");
    }
    chunks.push(buffer);
  }
  if (chunks.length === 0) {
    throw new BodyError("empty request body");
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new BodyError("request body is not JSON");
  }
}

function createMcpServer(
  deps: ReviewsServerDeps,
  claims: TokenClaims,
  tenant: TenantSettings,
): McpServer {
  const server = new McpServer(
    { name: "reviews", version: "1.0.0" },
    {
      instructions:
        "Read-only Olist review data, scoped to the tenant's sellers. Dates are " +
        "YYYY-MM-DD and inclusive. Every result carries summary, rows, " +
        "total_rows and audit_id.",
    },
  );
  registerReviewTools(server, {
    claims,
    tenant,
    manifest: deps.manifest,
    pool: deps.pool,
  });
  return server;
}

async function health(res: ServerResponse, deps: ReviewsServerDeps): Promise<void> {
  let databaseOk = false;
  try {
    await deps.pool.query("SELECT 1");
    databaseOk = true;
  } catch {
    databaseOk = false;
  }
  respondJson(
    res,
    databaseOk ? 200 : 503,
    {
      status: databaseOk ? "ok" : "error",
      service: "mcp-reviews",
      database: databaseOk,
      tools: deps.manifest.size,
    },
  );
}

export function createReviewsHttpServer(deps: ReviewsServerDeps): Server {
  return createServer((req, res) => {
    void (async (): Promise<void> => {
      const path = new URL(req.url ?? "/", "http://localhost").pathname;
      if (path === "/health") {
        if (req.method !== "GET") {
          res.writeHead(405, { allow: "GET" });
          res.end();
          return;
        }
        await health(res, deps);
        return;
      }
      if (path !== "/mcp") {
        respondJson(res, 404, { error: "not found" });
        return;
      }
      if (req.method !== "POST") {
        // Stateless mode has no server-to-client SSE stream to open.
        res.writeHead(405, { allow: "POST" });
        res.end();
        return;
      }

      let claims: TokenClaims;
      try {
        claims = verifyRunToken(bearerToken(req.headers.authorization), deps.secret);
      } catch {
        // Generic on purpose: the reason, the token and the tenant never leak.
        res.setHeader("www-authenticate", "Bearer");
        respondJson(res, 401, { error: "unauthorized" });
        return;
      }
      const tenant = deps.tenants.get(claims.tenantId);
      if (!tenant) {
        respondJson(res, 403, { error: "forbidden" });
        return;
      }

      let body: unknown;
      try {
        body = await readJsonBody(req);
      } catch {
        respondJson(res, 400, { error: "bad request" });
        return;
      }

      const server = createMcpServer(deps, claims, tenant);
      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: undefined,
        enableJsonResponse: true,
      });
      res.on("close", () => {
        void transport.close().catch(() => undefined);
        void server.close().catch(() => undefined);
      });
      await server.connect(transport);
      await transport.handleRequest(req, res, body);
    })().catch((error: unknown) => {
      console.error(`request failed: ${(error as Error).message}`);
      if (!res.headersSent) {
        respondJson(res, 500, { error: "internal error" });
      } else if (!res.writableEnded) {
        res.end();
      }
    });
  });
}

export type { ReviewsServerDeps as ServerDeps };
