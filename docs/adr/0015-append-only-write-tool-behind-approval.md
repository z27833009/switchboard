# ADR 0015: One append-only write tool behind a human approval gate

Status: accepted, 2026-11-13. Ships in T3.01 (AC-15a, AC-15b). Amends ADR 0005, which made every tool read-only.

## Context

ADR 0005 and design 7.6 keep every tool read-only: parameterized functions, a SELECT-only role, read-only transactions, a statement timeout, a row cap and an audit log. That is the containment story of the project, and it is what makes a demo tool server safe to expose.

The v2 roadmap promises the other half of the safety story: a tool that does change something, and a human decision before it does. Without one, "human in the loop" is a hook nobody can press in the demo. With one, the interesting question is how to add a write path without giving up the read-only default or letting the model decide on its own.

## Decision

- **Exactly one write tool.** `add_order_note@1` appends one row (order id, note text, tenant id, timestamp) to `olist.order_notes`. It never changes an order status, a price, an address or any existing row, and the table has no update or delete grant for any role. The blast radius of a bad call is one visible note.
- **The table is separate and append-only.** `docker/postgres/init/08-hitl.sql` creates it with a check constraint (1 to 500 characters) and gives `switchboard_tools` SELECT and INSERT only; `switchboard_app` gets nothing. The tool role is still SELECT-only everywhere else, so a bug in the server cannot touch `olist.orders`.
- **One read-write transaction, only for this tool.** The dispatcher picks `run_readwrite` when the manifest says `risk: write`, keeps the 5 s statement timeout, and caps the result at one row. Every other tool keeps `SET TRANSACTION READ ONLY`.
- **The write needs a human decision before it runs.** The manifest marks it `risk: write`, and a write tool is only allowed in a tenant's `tools.ask`. Both the API (`validate_tenant_permissions`) and the MCP server (`load_tenants`) refuse a write tool in `tools.allow`, so no configuration can skip the gate. Only the LangGraph path offers ask tools to the model, and it calls LangGraph `interrupt` before executing one; the run is persisted as `waiting_approval` and resumed (or rejected) through `POST /runs/{id}/approve` or `/reject`.
- **The tenant comes from the token, the order from the tool's own check.** The tool takes `order_id` and `note`; the tenant and seller filter come from the signed run token and the server's tenant config. The order has to belong to one of the tenant's sellers, checked in the same transaction, so the model cannot note another seller's order.
- **Audit unchanged.** Every call, allowed or denied, writes a `tool_audit` row with the tenant, run id, tool, version, argument hash and status, so an approved note is traceable to the run that wrote it and to its audit id.

## Alternatives

- **A general write tool with free-form SQL or DML.** Rejected: the containment claim collapses, and a demo cannot show a rollback story it does not have.
- **Update an order status behind approval.** Rejected for the demo: it changes business data that other tools read, so every later answer in the demo could depend on it. An append-only note shows the same approval path with a reversible story (the row is one row, and the table is auditable).
- **An approval claim inside the run token.** The cleanest cryptographic story: the API mints an approved token for the resumed call and the MCP server requires the claim for a write tool. Rejected for now because the graph holds the original context, the resumed call would need a token swap inside the paused state, and the API is currently the only process that mints tokens. Recorded here as the next step if a second write tool appears or if the tool server must be trusted with the approval decision.
- **An approval service or a queue (outbox, task runner).** The demo runs one API process; an in-process checkpointer is enough, and the limitation is stated (below).
- **A database-backed checkpointer.** LangGraph ships a Postgres saver, but it would add a dependency (`langgraph-checkpoint-postgres`) for a demo whose approvals last seconds. The port is ready for it; the limitation is named in the docs.

## Consequences

- The read-only default is unchanged for every other tool, and the test suite still proves the tool role cannot UPDATE or DELETE.
- A pending approval lives in the API process (an in-memory LangGraph checkpointer). An API restart loses it; the run row still says `waiting_approval`, and `POST /runs/{id}/approve` answers 409 with a clear message and marks the run `provider_error` rather than pretending to resume. Acceptable for the demo, named in design 6.7 and in progress.md.
- Resuming re-executes the paused graph node from its start (documented LangGraph behavior), so the model call and any read tool calls of that attempt run again before the gated call. The gated call itself runs exactly once: the interrupt always precedes it, and a repeated request id is refused at the API by one atomic claim.
- The approval decision is recorded in the run row (`pending_approval` is cleared, the status changes) and in the trace (`switchboard.approval.*` span attributes). A rejection ends the run with status `rejected` and an answer that says nothing was written.
- The MCP server accepts an ask tool from any caller holding a valid run token; it cannot see the approval. The approval boundary is the orchestrator, which is the only component that calls a tool. This is acceptable while the token mint is the API's alone; a per-call approval claim (above) is the upgrade path.
- A `waiting_approval` run holds a checkpoint in memory until it is decided or the process ends; there is no expiry sweeper yet.
