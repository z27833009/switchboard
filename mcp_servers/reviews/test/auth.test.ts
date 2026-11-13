import assert from "node:assert/strict";
import { test } from "node:test";

import {
  AUDIENCE,
  ISSUER,
  TokenError,
  bearerToken,
  verifyRunToken,
} from "../src/auth.js";
import { OTHER_SECRET, SECRET, mintToken, unsignedToken } from "./helpers.js";

test("a valid token returns the tenant and run claims", () => {
  const token = mintToken();
  const claims = verifyRunToken(token, SECRET);
  assert.equal(claims.tenantId, "seller_a");
  assert.match(claims.runId, /^[0-9a-f-]{36}$/);
  assert.ok(claims.tokenId);
  assert.ok(claims.expiresAt > claims.issuedAt);
  assert.equal(AUDIENCE, "switchboard-mcp");
  assert.equal(ISSUER, "switchboard-api");
});

test("a token signed with another secret is rejected", () => {
  assert.throws(() => verifyRunToken(mintToken({}, { secret: OTHER_SECRET }), SECRET), TokenError);
});

test("a wrong audience or issuer is rejected", () => {
  assert.throws(() => verifyRunToken(mintToken({ aud: "mcp-olist" }), SECRET), TokenError);
  assert.throws(() => verifyRunToken(mintToken({ iss: "another-api" }), SECRET), TokenError);
});

test("the platform-wide audience also matches an audience array", () => {
  const claims = verifyRunToken(mintToken({ aud: [AUDIENCE, "extra"] }), SECRET);
  assert.equal(claims.tenantId, "seller_a");
});

test("an expired token is rejected even with a valid signature", () => {
  const now = Math.floor(Date.now() / 1000);
  assert.throws(() => verifyRunToken(mintToken({ iat: now - 200, exp: now - 10 }), SECRET), TokenError);
});

test("a future iat beyond the clock skew is rejected", () => {
  const now = Math.floor(Date.now() / 1000);
  assert.throws(() => verifyRunToken(mintToken({ iat: now + 3600, exp: now + 4000 }), SECRET), TokenError);
});

test("every required claim is enforced", () => {
  for (const claim of ["iss", "aud", "tenant_id", "run_id", "iat", "exp", "jti"]) {
    assert.throws(
      () => verifyRunToken(mintToken({ [claim]: undefined }), SECRET),
      TokenError,
      `missing ${claim}`,
    );
  }
});

test("run_id must be a UUID", () => {
  assert.throws(() => verifyRunToken(mintToken({ run_id: "not-a-uuid" }), SECRET), TokenError);
});

test("alg none and other algorithms are rejected before the signature check", () => {
  const payload = {
    iss: ISSUER,
    aud: AUDIENCE,
    tenant_id: "seller_a",
    run_id: "3f3a2b1c-4d5e-4f6a-8b7c-9d0e1f2a3b4c",
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 90,
    jti: "j",
  };
  assert.throws(
    () => verifyRunToken(unsignedToken({ alg: "none", typ: "JWT" }, payload), SECRET),
    /HS256/,
  );
  assert.throws(
    () =>
      verifyRunToken(
        unsignedToken({ alg: "HS512", typ: "JWT" }, payload),
        SECRET,
      ),
    /HS256/,
  );
});

test("malformed tokens are rejected", () => {
  assert.throws(() => verifyRunToken("only.two", SECRET), TokenError);
  assert.throws(() => verifyRunToken("a.b.c", SECRET), TokenError);
  assert.throws(() => verifyRunToken("", SECRET), TokenError);
});

test("a secret shorter than 32 bytes is refused", () => {
  assert.throws(() => verifyRunToken(mintToken(), "short"), /32 bytes/);
});

test("the bearer header is parsed case-insensitively", () => {
  assert.equal(bearerToken("Bearer abc.def.ghi"), "abc.def.ghi");
  assert.equal(bearerToken("bearer abc.def.ghi"), "abc.def.ghi");
  assert.throws(() => bearerToken(undefined), TokenError);
  assert.throws(() => bearerToken("Basic abc"), TokenError);
  assert.throws(() => bearerToken("Bearer "), TokenError);
});
