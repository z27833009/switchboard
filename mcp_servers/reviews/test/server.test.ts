import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { test } from "node:test";

import { loadManifest, loadTenants } from "../src/config.js";
import type { ClientLike, Database } from "../src/db.js";
import { createReviewsHttpServer } from "../src/server.js";
import { SECRET, mintToken } from "./helpers.js";

const MANIFEST = loadManifest(resolve("manifest.yaml"));
const TENANTS = loadTenants(resolve("../../config/tenants.yaml"));

interface AuditRow {
  status: string;
  tenantId: string;
  toolName: string;
}

function fakeDatabase(options: {
  summaryRows?: Record<string, unknown>[];
  reviewRows?: Record<string, unknown>[];
  fail?: boolean;
  audits?: AuditRow[];
}): Database {
  const audits = options.audits ?? [];
  const recordAudit = (text: string, values?: unknown[]): void => {
    if (text.includes("INSERT INTO public.tool_audit") && values) {
      audits.push({
        status: String(values[7]),
        tenantId: String(values[2]),
        toolName: String(values[3]),
      });
    }
  };
  const respond = (text: string): Record<string, unknown>[] => {
    if (options.fail) {
      throw new Error("database is down");
    }
    if (text.includes("INSERT INTO public.tool_audit")) {
      return [{ id: randomUUID() }];
    }
    if (text.includes("review_score::int AS review_score")) {
      return options.summaryRows ?? [];
    }
    if (text.includes("FROM olist.order_reviews r") && text.includes("review_id")) {
      return options.reviewRows ?? [];
    }
    return [];
  };
  const client: ClientLike = {
    async query<R extends Record<string, unknown>>(text: string, values?: unknown[]) {
      recordAudit(text, values);
      return { rows: respond(text) as R[] };
    },
    release() {},
  };
  return {
    async query<R extends Record<string, unknown>>(text: string, values?: unknown[]) {
      recordAudit(text, values);
      return { rows: respond(text) as R[] };
    },
    async connect() {
      return client;
    },
  };
}

async function withServer(
  database: Database,
  run: (port: number) => Promise<void>,
): Promise<void> {
  const server = createReviewsHttpServer({
    secret: SECRET,
    manifest: MANIFEST,
    tenants: TENANTS,
    pool: database,
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  const address = server.address() as AddressInfo;
  try {
    await run(address.port);
  } finally {
    await new Promise<void>((done) => server.close(() => done()));
  }
}

function rpc(id: number, method: string, params: Record<string, unknown>): unknown {
  return { jsonrpc: "2.0", id, method, params };
}

async function post(port: number, body: unknown, token?: string): Promise<Response> {
  return fetch(`http://127.0.0.1:${port}/mcp`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
}

test("health reports the database and the manifest size", async () => {
  await withServer(fakeDatabase({}), async (port) => {
    const response = await fetch(`http://127.0.0.1:${port}/health`);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      status: "ok",
      service: "mcp-reviews",
      database: true,
      tools: 2,
    });
  });
});

test("health is 503 when the database is unreachable", async () => {
  await withServer(fakeDatabase({ fail: true }), async (port) => {
    const response = await fetch(`http://127.0.0.1:${port}/health`);
    assert.equal(response.status, 503);
    assert.equal(((await response.json()) as { status: string }).status, "error");
  });
});

test("missing and forged tokens answer 401 with a generic body", async () => {
  await withServer(fakeDatabase({}), async (port) => {
    for (const token of [undefined, "forged.token.value"]) {
      const response = await post(port, rpc(1, "tools/list", {}), token);
      assert.equal(response.status, 401);
      assert.deepEqual(await response.json(), { error: "unauthorized" });
      assert.equal(response.headers.get("www-authenticate"), "Bearer");
    }
  });
});

test("a token for an unknown tenant is denied", async () => {
  await withServer(fakeDatabase({}), async (port) => {
    const response = await post(port, rpc(1, "tools/list", {}), mintToken({ tenant_id: "ghost" }));
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), { error: "forbidden" });
  });
});

test("a valid seller_a token lists the two tools and calls review_summary", async () => {
  const audits: AuditRow[] = [];
  await withServer(
    fakeDatabase({
      summaryRows: [
        { review_score: 1, reviews: 1 },
        { review_score: 5, reviews: 3 },
      ],
      audits,
    }),
    async (port) => {
      const token = mintToken();
      const list = await post(port, rpc(1, "tools/list", {}), token);
      assert.equal(list.status, 200);
      const listed = (await list.json()) as {
        result: { tools: { name: string }[] };
      };
      assert.deepEqual(listed.result.tools.map((tool) => tool.name).sort(), [
        "order_reviews@1",
        "review_summary@1",
      ]);

      const call = await post(
        port,
        rpc(2, "tools/call", {
          name: "review_summary@1",
          arguments: { date_from: "2018-01-01", date_to: "2018-03-31" },
        }),
        token,
      );
      assert.equal(call.status, 200);
      const body = (await call.json()) as {
        result: {
          structuredContent: {
            summary: { review_count: number; average_score: number; low_score_share: number };
            total_rows: number;
            audit_id: string;
          };
          isError?: boolean;
        };
      };
      assert.notEqual(body.result.isError, true);
      assert.equal(body.result.structuredContent.summary.review_count, 4);
      assert.equal(body.result.structuredContent.summary.average_score, 4);
      assert.equal(body.result.structuredContent.summary.low_score_share, 0.25);
      assert.equal(body.result.structuredContent.total_rows, 4);
      assert.match(body.result.structuredContent.audit_id, /^[0-9a-f-]{36}$/);
      assert.deepEqual(audits, [
        { status: "ok", tenantId: "seller_a", toolName: "review_summary" },
      ]);
    },
  );
});

test("order_reviews returns a clean empty result for a foreign order", async () => {
  const audits: AuditRow[] = [];
  await withServer(fakeDatabase({ reviewRows: [], audits }), async (port) => {
    const call = await post(
      port,
      rpc(1, "tools/call", { name: "order_reviews@1", arguments: { order_id: "foreign" } }),
      mintToken(),
    );
    assert.equal(call.status, 200);
    const body = (await call.json()) as {
      result: {
        structuredContent: {
          summary: { found: boolean; reviews: number };
          rows: unknown[];
          total_rows: number;
        };
      };
    };
    assert.deepEqual(body.result.structuredContent.rows, []);
    assert.equal(body.result.structuredContent.total_rows, 0);
    assert.equal(body.result.structuredContent.summary.found, false);
    assert.equal(body.result.structuredContent.summary.reviews, 0);
    assert.equal(audits[0]?.status, "ok");
  });
});

test("a tool outside the tenant allow list is denied and audited", async () => {
  const audits: AuditRow[] = [];
  await withServer(fakeDatabase({ audits }), async (port) => {
    const call = await post(
      port,
      rpc(1, "tools/call", {
        name: "review_summary@1",
        arguments: { date_from: "2018-01-01", date_to: "2018-03-31" },
      }),
      mintToken({ tenant_id: "default" }),
    );
    assert.equal(call.status, 200);
    const body = (await call.json()) as {
      result: { isError: boolean; content: { text: string }[] };
    };
    assert.equal(body.result.isError, true);
    assert.match(body.result.content[0]?.text ?? "", /not allowed/);
    assert.deepEqual(audits, [
      { status: "denied", tenantId: "default", toolName: "review_summary" },
    ]);
  });
});

test("a wrong audience is rejected like any other invalid token", async () => {
  await withServer(fakeDatabase({}), async (port) => {
    const response = await post(port, rpc(1, "tools/list", {}), mintToken({ aud: "mcp-olist" }));
    assert.equal(response.status, 401);
  });
});

test("GET /mcp is 405 because the transport is stateless", async () => {
  await withServer(fakeDatabase({}), async (port) => {
    const response = await fetch(`http://127.0.0.1:${port}/mcp`, { method: "GET" });
    assert.equal(response.status, 405);
  });
});
