# ADR 0014: Signed run tokens between the API and the tool server

Status: accepted, 2026-11-13. Proposed 2026-11-12. Amends ADR 0005. HS256 run tokens, server-side tenant resolution, and rejection plus audit of missing, forged and expired tokens ship with tests. T3.08 (2026-11-13) keeps verification in each MCP server and makes the audience platform-wide; see the update at the end.

## Context

ADR 0005 states that the MCP server enforces the tenant allowlist itself because the client side is not a trust boundary. In design 0.1 the server learned the tenant from request metadata sent by the client. A server that checks a caller's claim against a list, without knowing whether the claim is true, is not a trust boundary either: any process that can reach the port can claim to be the unrestricted `default` tenant and read every seller's orders.

## Decision

- At the start of each run the API mints a run token: a JWT signed with HS256 and a shared secret (`SWITCHBOARD_RUN_TOKEN_SECRET`, at least 32 bytes), with claims `iss`, `aud`, `tenant_id`, `run_id`, `iat`, `exp` (run timeout plus 30 seconds) and `jti`. The audience is `switchboard-mcp`, a platform-wide value, so one token is valid at every MCP server.
- `McpToolSource` sends it as `Authorization: Bearer <token>` on the MCP HTTP transport.
- The MCP server verifies signature, audience and expiry; takes tenant and run id only from the token; ignores tenant or seller fields in arguments or metadata; and loads allowlist and seller ids from its own copy of the tenant config.
- Missing, invalid and expired tokens are rejected and audited. The client maps the rejection to `RunTokenError` and the run ends with status `auth_error`.
- PyJWT is added as a dependency to both the API and the MCP server.

## Alternatives

- **Trust the network.** Keep the server on an internal Docker network and accept metadata. Rejected: the design would claim a boundary it does not have, and the claim is easy to test.
- **Mutual TLS between API and server.** Authenticates the API as a caller but says nothing about which tenant a call is for; the tenant would still be a claim. More setup on Windows Docker for less benefit.
- **An MCP gateway in front of the server** (for example IBM's ContextForge, which centralizes JWT authentication, permissions and observability for many MCP servers). The right shape with several tool servers; with one server it is an extra process without a second consumer. Revisited when a second tool server arrives (T3.08).
- **OAuth 2.1 as in the MCP authorization specification.** Designed for third-party clients obtaining user consent. Switchboard's API is a first-party caller acting for a tenant, so a shared-secret token is sufficient; the token format would carry over if OAuth were adopted later.
- **Asymmetric signing (RS256 or EdDSA).** Lets the server verify without being able to mint. Worth it when verifiers are more numerous or less trusted than the issuer; here there is one of each.

## Consequences

- About 30 lines on each side and four tests (no token, forged, expired, tenant in arguments ignored).
- The tool server cannot be called directly with a curl command any more; a small CLI helper mints a development token.
- The secret is a new operational item: present in `.env.example`, required at startup, moved to Key Vault in v2.
- `/chat` itself remains unauthenticated, which is a stated non-goal of the demo.

## Update 2026-11-13: a second MCP server (T3.08)

T3.08 adds `mcp_servers/reviews`, a TypeScript server exposing review tools, so this ADR's revisit condition ("when a second tool server arrives") is met. Decision: **verification stays in each MCP server; no gateway.**

- The audience becomes platform-wide, `switchboard-mcp`, in the API minting (`switchboard.api.run_tokens`), the Olist verifier (`mcp_servers/olist/auth.py`) and the reviews verifier (`mcp_servers/reviews/src/auth.ts`). Server-specific audiences would force the API to mint one token per source per run for no security gain: both servers share one secret and one caller, and the trust question is "which tenant", not "which server". A single value also means a new source cannot be reached with a stale audience.
- A gateway in front of the servers would still need to know each server's tool and tenant rules, or it would authenticate once and forward a verified tenant claim that each server must trust without seeing the signature. That moves the boundary without removing it. With two servers, the duplicated verifier is about 180 lines of TypeScript and about 70 lines of Python; keeping it server-local means every server checks the signature itself and no process has to be trusted to forward identity.
- A gateway becomes the better shape when the number of servers grows, when servers are operated by different teams, or when token revocation and rate limiting must be centralized. Those are later concerns, not demo concerns.
- The tenants file stays shared and each server enforces only the wire names it serves: the API checks every allowlist entry against the union of the manifests in `platform.yaml`, so a typo still fails startup, while an entry for another server is ignored by this one. A server that rejected foreign names would break as soon as a second source was allowed for one tenant.
- The reviews server writes the same `tool_audit` rows as the Olist server. It rejects a missing, invalid or expired token at the HTTP transport with 401 and a generic body; transport-level rejections have no trusted tenant or run id, so they are not audited (a tool-level denial is).
