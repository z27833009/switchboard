/**
 * HS256 run token verification (ADR 0014, T3.08).
 *
 * The token is the only source of tenant and run identity. Headers, tool
 * arguments and metadata are never trusted for identity. Verification uses
 * node:crypto only, so this server does not depend on a JWT package.
 */

import { createHmac, timingSafeEqual } from "node:crypto";

/** Platform-wide audience: one token is valid at every MCP server (ADR 0014). */
export const AUDIENCE = "switchboard-mcp";
export const ISSUER = "switchboard-api";
export const MIN_SECRET_BYTES = 32;
/** Clock skew tolerated for `iat`; `exp` is enforced strictly. */
export const CLOCK_SKEW_S = 60;

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class TokenError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TokenError";
  }
}

export interface TokenClaims {
  tenantId: string;
  runId: string;
  tokenId: string;
  issuedAt: number;
  expiresAt: number;
}

export interface VerifyOptions {
  audience?: string;
  issuer?: string;
  nowSeconds?: number;
}

function decodeJson(segment: string, what: string): Record<string, unknown> {
  let decoded: string;
  try {
    decoded = Buffer.from(segment, "base64url").toString("utf8");
  } catch {
    throw new TokenError(`invalid run token: ${what} is not base64url`);
  }
  let value: unknown;
  try {
    value = JSON.parse(decoded);
  } catch {
    throw new TokenError(`invalid run token: ${what} is not JSON`);
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TokenError(`invalid run token: ${what} is not a JSON object`);
  }
  return value as Record<string, unknown>;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function audienceMatches(claim: unknown, expected: string): boolean {
  if (typeof claim === "string") {
    return claim === expected;
  }
  return Array.isArray(claim) && claim.some((item) => item === expected);
}

/**
 * Verify signature, algorithm, issuer, audience and claims.
 *
 * Every failure raises `TokenError`; callers answer 401 with a generic body so
 * nothing about the reason, the token or the tenant leaks to the caller.
 */
export function verifyRunToken(
  token: string,
  secret: string,
  options: VerifyOptions = {},
): TokenClaims {
  if (Buffer.byteLength(secret, "utf8") < MIN_SECRET_BYTES) {
    throw new TokenError("run token secret is shorter than 32 bytes");
  }
  const audience = options.audience ?? AUDIENCE;
  const issuer = options.issuer ?? ISSUER;
  const now = options.nowSeconds ?? Math.floor(Date.now() / 1000);

  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new TokenError("invalid run token: expected three segments");
  }
  const [headerSegment, payloadSegment, signatureSegment] = parts as [
    string,
    string,
    string,
  ];
  if (!headerSegment || !payloadSegment || !signatureSegment) {
    throw new TokenError("invalid run token: empty segment");
  }

  const header = decodeJson(headerSegment, "header");
  // "alg": "none" is rejected here, before any signature work.
  if (header.alg !== "HS256") {
    throw new TokenError("invalid run token: algorithm must be HS256");
  }
  if (header.typ !== undefined && header.typ !== "JWT") {
    throw new TokenError("invalid run token: unexpected token type");
  }

  const expected = createHmac("sha256", secret)
    .update(`${headerSegment}.${payloadSegment}`)
    .digest();
  let provided: Buffer;
  try {
    provided = Buffer.from(signatureSegment, "base64url");
  } catch {
    throw new TokenError("invalid run token: signature is not base64url");
  }
  if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
    throw new TokenError("invalid run token: signature does not match");
  }

  const claims = decodeJson(payloadSegment, "payload");
  if (claims.iss !== issuer) {
    throw new TokenError("invalid run token: wrong issuer");
  }
  if (!audienceMatches(claims.aud, audience)) {
    throw new TokenError("invalid run token: wrong audience");
  }

  const expiresAt = claims.exp;
  const issuedAt = claims.iat;
  if (typeof expiresAt !== "number" || !Number.isFinite(expiresAt)) {
    throw new TokenError("invalid run token: missing exp");
  }
  if (typeof issuedAt !== "number" || !Number.isFinite(issuedAt)) {
    throw new TokenError("invalid run token: missing iat");
  }
  if (expiresAt <= now) {
    throw new TokenError("run token expired");
  }
  if (issuedAt > now + CLOCK_SKEW_S) {
    throw new TokenError("invalid run token: iat is in the future");
  }
  if (expiresAt <= issuedAt) {
    throw new TokenError("invalid run token: exp is not after iat");
  }

  const tenantId = claims.tenant_id;
  const runId = claims.run_id;
  const tokenId = claims.jti;
  if (!isNonEmptyString(tenantId)) {
    throw new TokenError("invalid run token: missing tenant_id");
  }
  if (!isNonEmptyString(runId) || !UUID_PATTERN.test(runId)) {
    throw new TokenError("invalid run token: run_id must be a UUID");
  }
  if (!isNonEmptyString(tokenId)) {
    throw new TokenError("invalid run token: missing jti");
  }

  return { tenantId, runId, tokenId, issuedAt, expiresAt };
}

/** Extract the bearer token from an Authorization header, or raise. */
export function bearerToken(header: string | string[] | undefined): string {
  const value = Array.isArray(header) ? header[0] : header;
  if (!value || !value.toLowerCase().startsWith("bearer ")) {
    throw new TokenError("missing bearer run token");
  }
  const token = value.slice(7).trim();
  if (!token) {
    throw new TokenError("empty bearer run token");
  }
  return token;
}
