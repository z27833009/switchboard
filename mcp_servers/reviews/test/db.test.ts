import assert from "node:assert/strict";
import { test } from "node:test";

import {
  type ClientLike,
  type Database,
  ORDER_REVIEWS_SQL,
  REVIEW_SUMMARY_SQL,
  hashArgs,
  insertAudit,
  redactArgs,
  runOrderReviews,
  runReviewSummary,
  withReadonlyTransaction,
} from "../src/db.js";

interface Call {
  text: string;
  values?: unknown[];
}

function fakeClient(
  responder: (text: string, values?: unknown[]) => Record<string, unknown>[],
  calls: Call[] = [],
): ClientLike {
  return {
    async query<R extends Record<string, unknown>>(text: string, values?: unknown[]) {
      calls.push({ text, values });
      return { rows: responder(text, values) as R[] };
    },
    release() {
      // no-op
    },
  };
}

test("review_summary scopes through order_items and never reads a tenant argument", async () => {
  const calls: Call[] = [];
  const client = fakeClient(() => [
    { review_score: 1, reviews: 2 },
    { review_score: 4, reviews: 6 },
    { review_score: 5, reviews: 2 },
  ], calls);
  const outcome = await runReviewSummary(client, ["seller-1"], {
    dateFrom: "2018-01-01",
    dateTo: "2018-03-31",
  });
  const call = calls[0];
  assert.ok(call);
  assert.match(call.text, /olist\.order_reviews/);
  assert.match(call.text, /olist\.order_items/);
  assert.match(call.text, /i\.seller_id = ANY/);
  assert.deepEqual(call.values, ["2018-01-01", "2018-03-31", ["seller-1"], null]);
  assert.deepEqual(outcome.summary.score_distribution, {
    "1": 2,
    "2": 0,
    "3": 0,
    "4": 6,
    "5": 2,
  });
  assert.equal(outcome.summary.review_count, 10);
  assert.equal(outcome.summary.average_score, 3.6);
  assert.equal(outcome.summary.low_score_count, 2);
  assert.equal(outcome.summary.low_score_share, 0.2);
  assert.equal(outcome.totalRows, 10);
  assert.equal(outcome.rows.length, 5);
});

test("an unrestricted tenant passes a null seller list and an empty result is clean", async () => {
  const calls: Call[] = [];
  const client = fakeClient(() => [], calls);
  const outcome = await runReviewSummary(client, null, {
    dateFrom: "2018-01-01",
    dateTo: "2018-01-31",
    minScore: 3,
  });
  assert.deepEqual(calls[0]?.values, ["2018-01-01", "2018-01-31", null, 3]);
  assert.equal(outcome.summary.review_count, 0);
  assert.equal(outcome.summary.average_score, null);
  assert.equal(outcome.summary.low_score_share, null);
  assert.equal(outcome.totalRows, 0);
});

test("order_reviews filters the order through the tenant's sellers", async () => {
  const calls: Call[] = [];
  let rows: Record<string, unknown>[] = [
    {
      review_id: "r1",
      review_score: 5,
      review_comment_title: "ok",
      review_comment_message: "fine",
      review_creation_date: "2018-03-05T10:00:00",
      review_answer_timestamp: "2018-03-06T09:00:00",
    },
  ];
  const client = fakeClient(() => rows, calls);
  const found = await runOrderReviews(client, ["seller-1"], { orderId: "order-1" });
  assert.match(ORDER_REVIEWS_SQL, /i\.seller_id = ANY/);
  assert.deepEqual(calls[0]?.values, ["order-1", ["seller-1"]]);
  assert.equal(found.summary.found, true);
  assert.equal(found.summary.reviews, 1);
  assert.equal(found.summary.average_score, 5);
  assert.equal(found.rows[0]?.created_at, "2018-03-05T10:00:00");

  rows = [];
  const foreign = await runOrderReviews(client, ["seller-1"], { orderId: "foreign" });
  assert.deepEqual(foreign.rows, []);
  assert.equal(foreign.totalRows, 0);
  assert.equal(foreign.summary.found, false);
  assert.equal(foreign.summary.reviews, 0);
  assert.equal(foreign.summary.average_score, null);
});

test("the summary SQL reads no table outside the Olist schema", () => {
  assert.match(REVIEW_SUMMARY_SQL, /FROM olist\.orders/);
  assert.match(REVIEW_SUMMARY_SQL, /FROM olist\.order_reviews/);
  assert.doesNotMatch(REVIEW_SUMMARY_SQL, /tool_audit/);
});

test("withReadonlyTransaction sets a read-only transaction and releases", async () => {
  const calls: string[] = [];
  let released = false;
  const client: ClientLike = {
    async query<R extends Record<string, unknown>>(text: string) {
      calls.push(text);
      return { rows: [] as R[] };
    },
    release() {
      released = true;
    },
  };
  const database: Database = {
    async query<R extends Record<string, unknown>>() {
      return { rows: [] as R[] };
    },
    async connect() {
      return client;
    },
  };
  const result = await withReadonlyTransaction(database, async (connection) => {
    await connection.query("SELECT 1");
    return "done";
  });
  assert.equal(result, "done");
  assert.deepEqual(calls, ["BEGIN TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 5000", "SELECT 1", "COMMIT"]);
  assert.equal(released, true);
});

test("args hash is stable and redaction hides declared keys", async () => {
  assert.equal(hashArgs({ b: 1, a: 2 }), hashArgs({ a: 2, b: 1 }));
  assert.notEqual(hashArgs({ a: 1 }), hashArgs({ a: 2 }));
  assert.deepEqual(redactArgs({ order_id: "x", score: 3 }, ["order_id"]), {
    order_id: "[redacted]",
    score: 3,
  });

  const calls: Call[] = [];
  const client = fakeClient(() => [{ id: "unused" }], calls);
  const id = await insertAudit(client, {
    runId: "3f3a2b1c-4d5e-4f6a-8b7c-9d0e1f2a3b4c",
    tenantId: "seller_a",
    toolName: "review_summary",
    toolVersion: "1",
    args: { date_from: "2018-01-01", tenant_id: "default" },
    sensitiveArgs: ["tenant_id"],
    status: "ok",
    durationMs: 4,
  });
  assert.match(id, /^[0-9a-f-]{36}$/);
  const call = calls[0];
  assert.ok(call);
  assert.deepEqual(call.values?.slice(0, 5), [
    id,
    "3f3a2b1c-4d5e-4f6a-8b7c-9d0e1f2a3b4c",
    "seller_a",
    "review_summary",
    "1",
  ]);
  assert.match(String(call.values?.[6]), /"tenant_id":"\[redacted\]"/);
});
