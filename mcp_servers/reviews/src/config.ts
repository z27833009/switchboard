/**
 * Manifest and tenant configuration for the reviews MCP server.
 *
 * The server is a separate process and deliberately does not import the Python
 * platform. It validates the parts its trust boundary depends on: the manifest
 * it serves, the tenant allowlists and the seller ids. The tenants file is
 * shared by every MCP server, so a name this server does not serve belongs to
 * another source and is ignored here; the API validates the union of every
 * manifest declared in platform.yaml at startup (design.md sections 7.2, 7.3
 * and 11).
 */

import { readFileSync } from "node:fs";
import { parse as parseYaml } from "yaml";

const WIRE_NAME_PATTERN = /^[a-z][a-z0-9_]*@[0-9]+$/;
const TENANT_KEYS = new Set(["provider", "agents", "tools", "seller_ids", "retrieval", "cache"]);
const AGENT_KEYS = new Set(["allow"]);
const TOOL_KEYS = new Set(["allow", "ask"]);
const RETRIEVAL_KEYS = new Set(["k", "min_score", "rewrite", "rewrite_mode", "search"]);
const CACHE_KEYS = new Set(["enabled", "threshold", "ttl_days"]);

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

export interface ToolDefinition {
  readonly name: string;
  readonly version: string;
  readonly description: string;
  readonly risk: "read" | "write";
  readonly tenantScoped: boolean;
  readonly sensitiveArgs: readonly string[];
}

export interface TenantSettings {
  /** `null` means every seller (the unrestricted demo tenant). */
  readonly sellerIds: readonly string[] | null;
  /** Wire names the tenant may call. Foreign names belong to other servers. */
  readonly allowedTools: ReadonlySet<string>;
  readonly askTools: ReadonlySet<string>;
}

export function wireName(tool: ToolDefinition): string {
  return `${tool.name}@${tool.version}`;
}

function readYaml(path: string): Record<string, unknown> {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (error) {
    throw new ConfigError(`cannot read ${path}: ${(error as Error).message}`);
  }
  let value: unknown;
  try {
    value = parseYaml(text);
  } catch (error) {
    throw new ConfigError(`${path} is not valid YAML: ${(error as Error).message}`);
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ConfigError(`${path} must contain a YAML mapping`);
  }
  return value as Record<string, unknown>;
}

function isMapping(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function checkKnownKeys(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
  field: string,
): void {
  const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  if (unknown.length > 0) {
    throw new ConfigError(
      `${field} has unknown keys [${unknown.sort().join(", ")}]; allowed: [${[...allowed].sort().join(", ")}]`,
    );
  }
}

function names(value: unknown, field: string): string[] {
  if (!Array.isArray(value)) {
    throw new ConfigError(`${field} must be a list`);
  }
  const cleaned = value.map((item) => {
    if (typeof item !== "string") {
      throw new ConfigError(`${field} entries must be strings`);
    }
    const trimmed = item.trim();
    if (!trimmed) {
      throw new ConfigError(`${field} must not contain empty names`);
    }
    return trimmed;
  });
  const duplicates = [...new Set(cleaned.filter((name, index) => cleaned.indexOf(name) !== index))];
  if (duplicates.length > 0) {
    throw new ConfigError(`${field} contains duplicates: ${duplicates.sort().join(", ")}`);
  }
  return cleaned;
}

function toolNames(value: unknown, field: string): string[] {
  const entries = names(value, field);
  for (const name of entries) {
    if (!WIRE_NAME_PATTERN.test(name)) {
      throw new ConfigError(
        `${field} entry "${name}" is not a wire name (expected name@major)`,
      );
    }
  }
  return entries;
}

/**
 * Load the manifest the server serves.
 *
 * Every entry is read-only: this server has no write tool, and declaring one
 * would be a configuration error rather than a silent capability.
 */
export function loadManifest(path: string): Map<string, ToolDefinition> {
  const payload = readYaml(path);
  const entries = payload.tools;
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new ConfigError(`${path} has no tools list`);
  }
  const tools = new Map<string, ToolDefinition>();
  for (const entry of entries) {
    if (!isMapping(entry)) {
      throw new ConfigError(`${path} has a non-mapping tool entry`);
    }
    if (typeof entry.name !== "string" || typeof entry.version !== "string") {
      throw new ConfigError(`${path} has a tool without a name and version`);
    }
    if (typeof entry.description !== "string" || !entry.description.trim()) {
      throw new ConfigError(`${path} tool "${entry.name}" has no description`);
    }
    const risk = entry.risk ?? "read";
    if (risk !== "read") {
      throw new ConfigError(`${path} tool ${entry.name} must be read-only; found ${String(risk)}`);
    }
    const tool: ToolDefinition = {
      name: entry.name,
      version: entry.version,
      description: entry.description,
      risk,
      tenantScoped: entry.tenant_scoped === true,
      sensitiveArgs: Array.isArray(entry.sensitive_args)
        ? entry.sensitive_args.map((item) => String(item))
        : [],
    };
    const wire = wireName(tool);
    if (tools.has(wire)) {
      throw new ConfigError(`${path} declares ${wire} twice`);
    }
    tools.set(wire, tool);
  }
  return tools;
}

/**
 * Load the tenant allowlists and seller scope from the shared tenants file.
 *
 * Unknown tool names are not rejected: the file lists tools from every MCP
 * server, and this server only serves its own manifest. The API checks every
 * allowlist entry against the union of the manifests, so a typo still fails a
 * process at startup.
 */
export function loadTenants(path: string): Map<string, TenantSettings> {
  const payload = readYaml(path);
  const entries = payload.tenants;
  if (!isMapping(entries) || Object.keys(entries).length === 0) {
    throw new ConfigError(`${path} has no tenants mapping`);
  }
  const tenants = new Map<string, TenantSettings>();
  for (const [rawId, entry] of Object.entries(entries)) {
    const tenantId = rawId.trim();
    if (!tenantId) {
      throw new ConfigError(`${path} has an empty tenant id`);
    }
    if (!isMapping(entry)) {
      throw new ConfigError(`tenant ${tenantId} is not a mapping`);
    }
    checkKnownKeys(entry, TENANT_KEYS, `tenant ${tenantId}`);

    if (entry.provider !== undefined) {
      if (typeof entry.provider !== "string" || !entry.provider.trim()) {
        throw new ConfigError(`tenant ${tenantId} provider must be a non-empty string`);
      }
    }

    const agents = entry.agents === undefined ? {} : entry.agents;
    if (!isMapping(agents)) {
      throw new ConfigError(`tenant ${tenantId} agents must be a mapping`);
    }
    checkKnownKeys(agents, AGENT_KEYS, `tenant ${tenantId} agents`);
    if (agents.allow !== undefined) {
      names(agents.allow, `tenant ${tenantId} agents.allow`);
    }

    const tools = entry.tools === undefined ? {} : entry.tools;
    if (!isMapping(tools)) {
      throw new ConfigError(`tenant ${tenantId} tools must be a mapping`);
    }
    checkKnownKeys(tools, TOOL_KEYS, `tenant ${tenantId} tools`);
    const allow = tools.allow === undefined ? [] : toolNames(tools.allow, `tenant ${tenantId} tools.allow`);
    const ask = tools.ask === undefined ? [] : toolNames(tools.ask, `tenant ${tenantId} tools.ask`);
    const overlap = allow.filter((name) => ask.includes(name));
    if (overlap.length > 0) {
      throw new ConfigError(
        `tenant ${tenantId} tools.allow and tools.ask share ${overlap.sort().join(", ")}; ` +
          "a tool is either allowed automatically or requires approval",
      );
    }

    const sellerIds = entry.seller_ids;
    let sellers: string[] | null = null;
    if (sellerIds !== undefined && sellerIds !== null) {
      sellers = names(sellerIds, `tenant ${tenantId} seller_ids`);
      if (sellers.length === 0) {
        throw new ConfigError(
          `tenant ${tenantId} seller_ids must be null (all sellers) or a non-empty list`,
        );
      }
      if (sellers.some((seller) => seller.includes(","))) {
        throw new ConfigError(`tenant ${tenantId} seller_ids must not contain commas`);
      }
    }

    for (const [field, allowed] of [
      ["retrieval", RETRIEVAL_KEYS],
      ["cache", CACHE_KEYS],
    ] as const) {
      if (entry[field] === undefined) {
        continue;
      }
      const settings = entry[field];
      if (!isMapping(settings)) {
        throw new ConfigError(`tenant ${tenantId} ${field} must be a mapping`);
      }
      checkKnownKeys(settings, allowed, `tenant ${tenantId} ${field}`);
    }

    tenants.set(tenantId, {
      sellerIds: sellers,
      allowedTools: new Set(allow),
      askTools: new Set(ask),
    });
  }
  return tenants;
}
