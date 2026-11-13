/**
 * Database access for the review tools.
 *
 * Every query runs in a read-only transaction with a 5 second statement
 * timeout through the SELECT-only `switchboard_tools` role (design.md section
 * 7.1). The tenant's seller ids always come from the server-side tenant file,
 * never from tool arguments, and the filter is applied through
 * `olist.order_items`.
 */

import { createHash, randomUUID } from "node:crypto";

export const ROW_CAP = 200;
export const STATEMENT_TIMEOUT_MS = 5000;

export interface Queryable {
  query<R extends Record<string, unknown>>(
    text: string,
    values?: unknown[],
  ): Promise<{ rows: R[] }>;
}

/** The part of `pg.Pool` the tools use; a fake database can implement it. */
export interface Database extends Queryable {
  connect(): Promise<ClientLike>;
}

export interface ClientLike extends Queryable {
  release(): void;
}

export interface ToolOutcome {
  summary: Record<string, unknown>;
  rows: Record<string, unknown>[];
  totalRows: number;
}

export interface ReviewSummaryArgs {
  dateFrom: string;
  dateTo: string;
  minScore?: number;
}

export interface OrderReviewsArgs {
  orderId: string;
}

export const REVIEW_SUMMARY_SQL = `
WITH scoped_orders AS (
    SELECT DISTINCT o.order_id
    FROM olist.orders o
    WHERE o.order_purchase_timestamp >= $1::date
      AND o.order_purchase_timestamp < ($2::date + interval '1 day')
      AND ($3::text[] IS NULL OR EXISTS (
            SELECT 1 FROM olist.order_items i
            WHERE i.order_id = o.order_id AND i.seller_id = ANY($3::text[])
      ))
)
SELECT r.review_score::int AS review_score, count(*)::int AS reviews
FROM olist.order_reviews r
JOIN scoped_orders s ON s.order_id = r.order_id
WHERE ($4::int IS NULL OR r.review_score >= $4)
GROUP BY r.review_score
ORDER BY r.review_score
LIMIT ${ROW_CAP}
`;

export const ORDER_REVIEWS_SQL = `
SELECT r.review_id,
       r.review_score::int AS review_score,
       r.review_comment_title,
       r.review_comment_message,
       to_char(r.review_creation_date, 'YYYY-MM-DD"T"HH24:MI:SS') AS review_creation_date,
       to_char(r.review_answer_timestamp, 'YYYY-MM-DD"T"HH24:MI:SS') AS review_answer_timestamp
FROM olist.order_reviews r
WHERE r.order_id = $1
  AND EXISTS (
        SELECT 1 FROM olist.order_items i
        WHERE i.order_id = r.order_id
          AND ($2::text[] IS NULL OR i.seller_id = ANY($2::text[]))
  )
ORDER BY r.review_creation_date, r.review_id
LIMIT ${ROW_CAP}
`;

/** Run `operation` on one connection inside a read-only, time-limited transaction. */
export async function withReadonlyTransaction<T>(
  pool: Database,
  operation: (client: ClientLike) => Promise<T>,
): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN TRANSACTION READ ONLY");
    await client.query(`SET LOCAL statement_timeout = ${STATEMENT_TIMEOUT_MS}`);
    const result = await operation(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // The original error is the useful one.
    }
    throw error;
  } finally {
    client.release();
  }
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}

function iso(value: unknown): string | null {
  if (value instanceof Date) {
    return value.toISOString();
  }
  if (typeof value === "string") {
    return value;
  }
  return null;
}

function sellerValues(sellerIds: readonly string[] | null): string[] | null {
  return sellerIds === null ? null : [...sellerIds];
}

export async function runReviewSummary(
  client: Queryable,
  sellerIds: readonly string[] | null,
  args: ReviewSummaryArgs,
): Promise<ToolOutcome> {
  const result = await client.query<{ review_score: number; reviews: number }>(
    REVIEW_SUMMARY_SQL,
    [args.dateFrom, args.dateTo, sellerValues(sellerIds), args.minScore ?? null],
  );
  const counts = new Map<number, number>();
  for (const row of result.rows) {
    counts.set(Number(row.review_score), Number(row.reviews));
  }
  const distribution: Record<string, number> = { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 };
  let reviewCount = 0;
  let scoreSum = 0;
  let lowScoreCount = 0;
  for (const score of [1, 2, 3, 4, 5]) {
    const count = counts.get(score) ?? 0;
    distribution[String(score)] = count;
    reviewCount += count;
    scoreSum += score * count;
    if (score <= 2) {
      lowScoreCount += count;
    }
  }
  const averageScore = reviewCount > 0 ? round4(scoreSum / reviewCount) : null;
  const lowScoreShare = reviewCount > 0 ? round4(lowScoreCount / reviewCount) : null;
  const rows = [1, 2, 3, 4, 5].map((score) => ({
    score,
    reviews: distribution[String(score)] ?? 0,
    share:
      reviewCount > 0 ? round4((distribution[String(score)] ?? 0) / reviewCount) : null,
  }));
  return {
    summary: {
      date_from: args.dateFrom,
      date_to: args.dateTo,
      min_score: args.minScore ?? null,
      review_count: reviewCount,
      average_score: averageScore,
      score_distribution: distribution,
      low_score_count: lowScoreCount,
      low_score_share: lowScoreShare,
    },
    rows,
    totalRows: reviewCount,
  };
}

export async function runOrderReviews(
  client: Queryable,
  sellerIds: readonly string[] | null,
  args: OrderReviewsArgs,
): Promise<ToolOutcome> {
  const result = await client.query<{
    review_id: string;
    review_score: number;
    review_comment_title: string | null;
    review_comment_message: string | null;
    review_creation_date: unknown;
    review_answer_timestamp: unknown;
  }>(ORDER_REVIEWS_SQL, [args.orderId, sellerValues(sellerIds)]);
  const rows = result.rows.map((row) => ({
    review_id: row.review_id,
    score: Number(row.review_score),
    title: row.review_comment_title,
    message: row.review_comment_message,
    created_at: iso(row.review_creation_date),
    answered_at: iso(row.review_answer_timestamp),
  }));
  const scoreSum = rows.reduce((total, row) => total + row.score, 0);
  return {
    summary: {
      order_id: args.orderId,
      found: rows.length > 0,
      reviews: rows.length,
      average_score: rows.length > 0 ? round4(scoreSum / rows.length) : null,
    },
    rows,
    totalRows: rows.length,
  };
}

export interface AuditInput {
  runId: string;
  tenantId: string;
  toolName: string;
  toolVersion: string;
  args: Record<string, unknown>;
  sensitiveArgs: readonly string[];
  status: "ok" | "denied" | "error";
  durationMs: number;
}

/** Stable JSON with sorted keys, so two equal argument objects hash equally. */
export function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
      a < b ? -1 : a > b ? 1 : 0,
    );
    return `{${entries
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

export function hashArgs(args: Record<string, unknown>): string {
  return createHash("sha256").update(stableJson(args), "utf8").digest("hex");
}

export function redactArgs(
  args: Record<string, unknown>,
  sensitive: readonly string[],
): Record<string, unknown> {
  const hidden = new Set(sensitive);
  return Object.fromEntries(
    Object.entries(args).map(([key, value]) => [key, hidden.has(key) ? "[redacted]" : value]),
  );
}

/** Append one audit row (design.md section 7.4); a failed write fails the call. */
export async function insertAudit(pool: Queryable, input: AuditInput): Promise<string> {
  // The id is generated here: the tools role has INSERT but not SELECT on
  // tool_audit, and a RETURNING clause would need SELECT to read it back.
  const auditId = randomUUID();
  await pool.query(
    `INSERT INTO public.tool_audit
        (id, run_id, tenant_id, tool_name, tool_version, args_hash,
         args_redacted, status, duration_ms)
     VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb, $8, $9)`,
    [
      auditId,
      input.runId,
      input.tenantId,
      input.toolName,
      input.toolVersion,
      hashArgs(input.args),
      JSON.stringify(redactArgs(input.args, input.sensitiveArgs)),
      input.status,
      input.durationMs,
    ],
  );
  return auditId;
}
