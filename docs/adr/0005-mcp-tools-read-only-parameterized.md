# ADR 0005: MCP tool server in Python with read-only parameterized tools

Status: proposed, 2026-11-11

## Context

Agents need tools over a business database. The tool layer must be a real boundary (protocol, process, permissions), read-only by default, and easy to extend. Language choice for the server and the question of free-form SQL versus parameterized tools were open.

## Decision

- Tools are served by a separate FastMCP (Python) process over streamable HTTP. The platform consumes them through a `ToolSource` port whose first adapter is an MCP client.
- Phase 1 tools are parameterized functions with JSON schemas, not free-form SQL. The database role has SELECT only, every call runs in a read-only transaction with a statement timeout and a row cap.
- A manifest file is the source of truth for tool metadata: version, risk level, tenant scoping, sensitive arguments.
- The server enforces the tenant allowlist itself; the client-side filter is a convenience, not a trust boundary.
- Every call writes an audit row before returning.

## Alternatives

- **TypeScript MCP server.** Would show polyglot MCP. Rejected for Phase 1 because the tools share database schema knowledge and test fixtures with the Python loader and eval suite; a TS server is planned in v2 as a second `ToolSource` over a different data source.
- **In-process Python tools without MCP.** Simplest. Rejected because the process boundary is what makes the permission model and audit credible, and because MCP is the interoperability story.
- **Free-form read-only SQL tool from the start.** More flexible, better for open-ended questions. Deferred to v2 behind sqlglot validation and a tenant flag, because tool-call accuracy is easier to measure on parameterized tools and the risk surface is smaller.

## Consequences

- Adding a tool: one function, one manifest entry, one dataset case. No change to core or orchestrator.
- Tool-call accuracy is measured as name plus argument match against pinned tool versions.
- Two processes must agree on trace context propagation and tenant metadata; this is covered by integration tests.
