/**
 * Process entry point: configuration, database readiness and HTTP listen.
 *
 * Start with `node dist/src/index.js`. Every secret and path comes from the
 * environment (design.md sections 13.2 and 16); nothing is hard-coded.
 */

import { Pool } from "pg";

import { AUDIENCE, MIN_SECRET_BYTES } from "./auth.js";
import { ConfigError, loadManifest, loadTenants } from "./config.js";
import { createReviewsHttpServer } from "./server.js";

function required(name: string): string {
  const value = process.env[name]?.trim() ?? "";
  if (!value) {
    throw new ConfigError(`${name} is not set`);
  }
  return value;
}

async function main(): Promise<void> {
  const secret = required("SWITCHBOARD_RUN_TOKEN_SECRET");
  if (Buffer.byteLength(secret, "utf8") < MIN_SECRET_BYTES) {
    throw new ConfigError(
      `SWITCHBOARD_RUN_TOKEN_SECRET must be at least ${MIN_SECRET_BYTES} bytes`,
    );
  }
  const dsn = required("SWITCHBOARD_TOOLS_DATABASE_URL");
  const tenantsFile = process.env.SWITCHBOARD_TENANTS_FILE?.trim() || "config/tenants.yaml";
  const manifestFile = process.env.SWITCHBOARD_REVIEWS_MANIFEST?.trim() || "manifest.yaml";
  const port = Number(process.env.MCP_REVIEWS_PORT?.trim() || "8002");

  const manifest = loadManifest(manifestFile);
  const tenants = loadTenants(tenantsFile);
  const pool = new Pool({
    connectionString: dsn,
    max: 5,
    connectionTimeoutMillis: 5000,
  });

  try {
    await pool.query("SELECT 1");
    const audit = await pool.query<{ table: string | null }>(
      "SELECT to_regclass('public.tool_audit')::text AS table",
    );
    if (!audit.rows[0]?.table) {
      throw new ConfigError(
        "public.tool_audit is missing; apply docker/postgres/init/02-platform.sql",
      );
    }
  } catch (error) {
    await pool.end();
    throw error;
  }

  const server = createReviewsHttpServer({ secret, manifest, tenants, pool });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "0.0.0.0", () => {
      console.log(
        `mcp-reviews listening on 0.0.0.0:${port} with ${manifest.size} tools, ` +
          `audience ${AUDIENCE}`,
      );
      resolve();
    });
  });

  const shutdown = (signal: string): void => {
    console.log(`mcp-reviews stopping on ${signal}`);
    server.close(() => {
      void pool.end().finally(() => process.exit(0));
    });
  };
  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
}

main().catch((error: unknown) => {
  console.error(`mcp-reviews failed to start: ${(error as Error).message}`);
  process.exit(1);
});
