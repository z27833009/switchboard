import { createHmac } from "node:crypto";
import { randomUUID } from "node:crypto";

export const SECRET = "s".repeat(32);
export const OTHER_SECRET = "o".repeat(32);
export const AUDIENCE = "switchboard-mcp";

export interface ClaimsOverrides {
  [key: string]: unknown;
}

function encode(value: unknown): string {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

export function mintToken(
  overrides: ClaimsOverrides = {},
  options: { secret?: string; header?: Record<string, unknown> } = {},
): string {
  const now = Math.floor(Date.now() / 1000);
  const header = options.header ?? { alg: "HS256", typ: "JWT" };
  const payload: Record<string, unknown> = {
    iss: "switchboard-api",
    aud: AUDIENCE,
    tenant_id: "seller_a",
    run_id: randomUUID(),
    iat: now,
    exp: now + 90,
    jti: randomUUID(),
    ...overrides,
  };
  const headerSegment = encode(header);
  const payloadSegment = encode(payload);
  const secret = options.secret ?? SECRET;
  const signature = createHmac("sha256", secret)
    .update(`${headerSegment}.${payloadSegment}`)
    .digest("base64url");
  return `${headerSegment}.${payloadSegment}.${signature}`;
}

export function unsignedToken(header: Record<string, unknown>, payload: unknown): string {
  // A non-empty placeholder signature keeps the segment count valid so the
  // algorithm check is what rejects the token, not the parser.
  return `${encode(header)}.${encode(payload)}.AAAA`;
}
