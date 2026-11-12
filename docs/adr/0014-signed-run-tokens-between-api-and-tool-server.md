# ADR 0014: Signed run tokens between the API and the tool server

Status: proposed, 2026-11-12. Amends ADR 0005.

## Context

ADR 0005 states that the MCP server enforces the tenant allowlist itself because the client side is not a trust boundary. In design 0.1 the server learned the tenant from request metadata sent by the client. A server that checks a caller's claim against a list, without knowing whether the claim is true, is not a trust boundary either: any process that can reach the port can claim to be the unrestricted `default` tenant and read every seller's orders.

## Decision

- At the start of each run the API mints a run token: a JWT signed with HS256 and a shared secret (`SWITCHBOARD_RUN_TOKEN_SECRET`, at least 32 bytes), with claims `iss`, `aud`, `tenant_id`, `run_id`, `iat`, `exp` (run timeout plus 30 seconds) and `jti`.
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
