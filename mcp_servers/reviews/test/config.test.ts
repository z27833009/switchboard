import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";

import { ConfigError, loadManifest, loadTenants, wireName } from "../src/config.js";

const MANIFEST = resolve("manifest.yaml");
const TENANTS = resolve("../../config/tenants.yaml");

function tenantsFile(body: string): string {
  const directory = mkdtempSync(join(tmpdir(), "reviews-tenants-"));
  const path = join(directory, "tenants.yaml");
  writeFileSync(path, body, "utf8");
  return path;
}

test("the reviews manifest declares exactly the two read-only tools", () => {
  const manifest = loadManifest(MANIFEST);
  assert.deepEqual([...manifest.keys()].sort(), ["order_reviews@1", "review_summary@1"]);
  for (const tool of manifest.values()) {
    assert.equal(tool.risk, "read");
    assert.equal(tool.tenantScoped, true);
    assert.equal(wireName(tool), `${tool.name}@${tool.version}`);
  }
  assert.deepEqual(manifest.get("review_summary@1")?.sensitiveArgs, []);
});

test("the committed tenant file scopes seller_a to the new tools and seller ids", () => {
  const tenants = loadTenants(TENANTS);
  const seller = tenants.get("seller_a");
  assert.ok(seller);
  assert.deepEqual(seller.sellerIds, ["955fee9216a65b617aa5c0531780ce60"]);
  assert.equal(seller.allowedTools.has("review_summary@1"), true);
  assert.equal(seller.allowedTools.has("order_reviews@1"), true);
  const defaultTenant = tenants.get("default");
  assert.ok(defaultTenant);
  assert.equal(defaultTenant.allowedTools.has("review_summary@1"), false);
  assert.equal(defaultTenant.allowedTools.has("order_reviews@1"), false);
  assert.equal(defaultTenant.sellerIds, null);
});

test("tools served by another MCP server are tolerated", () => {
  const path = tenantsFile(`
tenants:
  default:
    provider: lmstudio
    agents:
      allow: ["data"]
    tools:
      allow: ["orders_summary@1", "order_lookup@1"]
    seller_ids: null
`);
  const tenants = loadTenants(path);
  assert.equal(tenants.get("default")?.allowedTools.has("orders_summary@1"), true);
});

test("unknown keys, duplicates and allow/ask overlap fail", () => {
  assert.throws(
    () => loadTenants(tenantsFile("tenants:\n  default:\n    toolz:\n      allow: []\n")),
    /unknown keys/,
  );
  assert.throws(
    () =>
      loadTenants(
        tenantsFile(`
tenants:
  default:
    tools:
      allow: ["review_summary@1", "review_summary@1"]
`),
      ),
    /duplicates/,
  );
  assert.throws(
    () =>
      loadTenants(
        tenantsFile(`
tenants:
  default:
    tools:
      allow: ["review_summary@1"]
      ask: ["review_summary@1"]
`),
      ),
    /allowed automatically/,
  );
});

test("malformed wire names, empty seller ids and commas fail", () => {
  assert.throws(
    () =>
      loadTenants(
        tenantsFile(`
tenants:
  default:
    tools:
      allow: ["review_summary"]
`),
      ),
    /wire name/,
  );
  assert.throws(
    () => loadTenants(tenantsFile("tenants:\n  default:\n    seller_ids: []\n")),
    /non-empty list/,
  );
  assert.throws(
    () => loadTenants(tenantsFile('tenants:\n  default:\n    seller_ids: ["a,b"]\n')),
    /commas/,
  );
});

test("a manifest entry without a description or with a write risk fails", () => {
  const directory = mkdtempSync(join(tmpdir(), "reviews-manifest-"));
  const badRisk = join(directory, "bad-risk.yaml");
  writeFileSync(
    badRisk,
    "tools:\n  - name: x\n    version: '1'\n    description: d\n    risk: write\n",
    "utf8",
  );
  assert.throws(() => loadManifest(badRisk), ConfigError);
  const noDescription = join(directory, "no-description.yaml");
  writeFileSync(
    noDescription,
    "tools:\n  - name: x\n    version: '1'\n    description: ''\n",
    "utf8",
  );
  assert.throws(() => loadManifest(noDescription), ConfigError);
});
