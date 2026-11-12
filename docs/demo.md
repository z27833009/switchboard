# Five minute demo

This script shows the Phase 1 loop end to end: a traced routing decision, a document citation, a tool citation with its audit row, a merged plan, the measured evaluation reports, and the three edits that add a tool. Everything runs locally: Docker Desktop on the host, LM Studio on the host, no cloud account.

The timings below assume the stack is already up, both models are loaded and the data is loaded. Setup is in the [README quick start](../README.md#quick-start). The demo works with the 1000-order sample, but the full Olist download gives realistic counts and is what the screenshots in a live demo should use.

| Clock | Step | Time |
| --- | --- | --- |
| 0:00 | Health | 30 s |
| 0:30 | Document question | 45 s |
| 1:15 | Data question and audit row | 75 s |
| 2:30 | Mixed question | 60 s |
| 3:30 | Evaluation reports | 45 s |
| 4:15 | Add a tool | 45 s |

## Before the clock starts

1. Stack up and healthy: `docker compose ps` shows `postgres`, `mcp-olist`, `api` and `web` as healthy.
2. LM Studio is serving `qwen3-8b` and `bge-m3` on port 18080 with one parallel slot.
3. Data and documents are loaded (README steps 5 and 6).
4. Open these tabs or windows:
   - Chat: http://localhost:8080/#/chat
   - Run list: http://localhost:8080/#/runs
   - Reports: `evals/reports/routing-lmstudio.md` and `evals/reports/toolcall-lmstudio.md`
   - A terminal at the repository root for the health and audit commands
5. Keep the tenant selector on `default`.

The plan comes from the model, so it can differ between runs. The committed reports are the record of how often it is right, not a promise that every question gets the plan you expect.

## 0:00 Health (30 s)

```bash
curl -s http://localhost:8000/health
```

Expected: `"status":"ok"` with `ok` for `postgres`, `mcp_olist` and `lmstudio`. The endpoint returns 503 when a dependency is down and names it. Mention that the same report is available through the web proxy at http://localhost:8080/api/health, which is the path a browser deployment uses.

Optional, if there is time: open http://localhost:8000/docs and show the five routes (`/chat`, `/chat/stream`, `/runs`, `/runs/{id}/trace`, `/health`).

## 0:30 Document question (45 s)

In the chat page, ask:

> What is the return window for electronics?

Click **Send**. While the run is in progress the **Live steps** panel fills in as server-sent events arrive: `run.started`, `plan.decided` with plan `docs`, `agent.started` for `docs`, `chat qwen3-8b` work, `agent.finished`, `gate.checked` and `run.finished`. The answer panel then shows the status, the plan, the answer, the citation chips and the token counts.

Expected answer: 15 calendar days from delivery, with a `[d1]` marker. The document is `data/documents/returns-policy.md`. The answer is generated from the retrieved chunk, not from a hard-coded response.

Click the **doc d1** chip. A document citation has no span id and no audit id, so the trace opens unselected. Select `supervisor.plan` in the tree to show `switchboard.plan` and `switchboard.plan.reason`, then select `agent.docs` to show `switchboard.citations: 1`. The citation snippet is in the chip's tooltip and in the answer panel.

## 1:15 Data question and audit row (75 s)

Ask:

> How many orders were delivered late in March 2018? Give the count.

Click **Send**. Expected plan: `data`. The live steps include `tool.called` for `orders_late@1` with the row count and duration. The answer carries a `tool t1` citation chip. The exact count depends on the loaded data: the 1000-order sample and the full download give different counts. The number in the answer comes from the tool result, never from the prompt.

Check the arguments in the trace: `date_from` `2018-03-01`, `date_to` `2018-03-31`, `min_days_late` 1. The citation chip opens `#/runs/<run_id>/trace?audit=<audit_id>` and selects the tool span. Show:

- `gen_ai.tool.name`, `switchboard.tool.version`, `switchboard.tool.args_hash` and `switchboard.tool.total_rows`.
- `switchboard.tool.audit_id`, `switchboard.tenant_id` and `switchboard.run_id`.
- The `tool.result` event, which contains the summary, the preview rows, the total row count and the audit id. The pane renders the full stored JSON, and says so if the exporter truncated it at 64 KB.
- There are two spans named `execute_tool orders_late@1`, one from the API client and one from the MCP server, with the same audit id.

Show the audit row behind that id:

```bash
docker compose exec -T postgres psql -U switchboard -d switchboard \
  -c "SELECT tool_name, tool_version, args_redacted, status, duration_ms, created_at FROM public.tool_audit ORDER BY created_at DESC LIMIT 1;"
```

Expected: one `orders_late` row, version `1`, the three arguments, status `ok` and a duration in milliseconds. This is the row a later audit API (v1) will read.

If the model puts `[t1]` before the number instead of after it, click the chip anyway: the citation is what links the claim to the tool result.

## 2:30 Mixed question (60 s)

Ask:

> Were late deliveries in March 2018 eligible for compensation under the policy?

Click **Send**. Expected plan: `data+docs`. Two `agent.started` steps appear close together. In the trace, `agent.data` and `agent.docs` overlap in time, as do their `chat qwen3-8b` spans. The merged answer has a `data:` section and a `docs:` section. The docs section carries a `[d1]` citation to `data/documents/late-delivery-compensation.md` (at least three calendar days late, order not canceled, account in good standing, request within 60 calendar days).

Say what is measured: the routing report shows that all 10 `data+docs` test cases were predicted as `data` alone, so mixed plans are the weak part of the local router (plan accuracy 0.5385 overall). The wording above was chosen after that measurement and does produce `data+docs` on the current build, but the short wording from the roadmap, "Were late deliveries in March eligible for compensation?", returned plan `data` in a live run on 2026-11-12. If the short wording gives `data`, show it and use it as the honest example of the measured gap instead of pretending the merge happened.

The data section may repeat numbers without its own `[t1]` marker. The tool span is still in the trace, and the docs citation is the one returned in the merged answer. Do not claim both kinds of citation if the merge only returned one.

## 3:30 Evaluation reports (45 s)

Open `evals/reports/routing-lmstudio.md` and `evals/reports/toolcall-lmstudio.md`.

Routing: 39 of 39 test cases, plan accuracy 0.5385, Jaccard 0.6667, unambiguous 0.5294, ambiguous 0.6000. The plan confusion matrix is the slide to show:

```text
| Expected \ Actual | data | direct | docs | none |
| data              |   12 |      0 |    0 |    0 |
| data+docs         |   10 |      0 |    0 |    0 |
| direct            |    1 |      4 |    0 |    0 |
| docs              |    6 |      0 |    5 |    1 |
```

Tool call: 40 of 40 cases, tool name, argument and both accuracy 0.9500, 0.95 calls per case, no redundant calls. Latency is one model call per case, p50 2353 ms and p95 2991 ms for routing, p50 3407.5 ms and p95 3747.8 ms for tool call.

Say which numbers are measured and which are targets: both reports were measured on LM Studio 1.2.0 with qwen3-8b, dataset v1, no limit, on 2026-11-12. The routing report scored the 39 test cases from its test split; the toolcall report scored all 40 cases. The 0.75 routing target and the 0.60 tool-call target are targets in the requirements. Cost is null in the reports because local models have no API cost and the price table arrives in v1.

Then point at the CI badge in the README: the gate runs the mock provider, which replays recorded model output, so it measures the pipeline (parsing, plan validation, tool normalization, scoring), not the model. A prompt change changes the fixture key and forces a re-record. See `docs/adr/0008-evaluation-strategy.md`.

## 4:15 Add a tool (45 s)

Open three files:

- `mcp_servers/olist/manifest.yaml`, the `orders_late` entry.
- `mcp_servers/olist/db.py`, the `orders_late` query function.
- `mcp_servers/olist/server.py`, the `orders_late` MCP wrapper.

Explain that the manifest declares the name, version, description, risk, tenant scoping and JSON schema. The MCP wrapper validates the arguments, calls `_call_tool`, and the dispatcher verifies the run token, applies the tenant allowlist, runs the query in a read-only transaction with a 5 s timeout and a 200-row cap, writes the audit row, and emits the span. Nothing under `src/switchboard/core` or `src/switchboard/adapters/orchestrator` changes (AC-07a).

The roadmap's demo script names `mcp_servers/olist/tools/orders_late.py`. The implemented layout has no `tools/` directory: the query lives in `db.py` and the wrapper in `server.py`. The three edits in the appendix are the accurate version.

Adding an agent is also one class plus one entry point, but do not demo it in the five minutes. Point at `src/switchboard/agents/` and the `switchboard.agents` entry points in `pyproject.toml`.

## Appendix: add a tool in three steps

This walkthrough adds `orders_by_state@1`, a read-only tool that counts orders by customer state for a purchase-date range. Do it on a scratch branch and revert it afterwards; nothing is committed. It follows the existing code and does not touch core or the orchestrator.

### Step 1: declare the tool in the manifest

Add this entry to `mcp_servers/olist/manifest.yaml`, next to the other tools:

```yaml
  - name: orders_by_state
    version: "1"
    description: Count orders by customer state for a purchase-date range.
    risk: read
    tenant_scoped: true
    sensitive_args: []
    input_schema:
      type: object
      properties:
        date_from:
          type: string
          format: date
        date_to:
          type: string
          format: date
      required: [date_from, date_to]
      additionalProperties: false
```

The manifest is the source of the wire name (`orders_by_state@1`), the schema the model sees, the risk level and the metadata sent over MCP.

### Step 2: add the query function

Add this function to `mcp_servers/olist/db.py`, after `orders_late`:

```python
async def orders_by_state(
    connection: asyncpg.Connection,
    tenant: TenantSettings,
    date_from: date,
    date_to: date,
) -> ToolOutcome:
    sellers = seller_filter(tenant)
    records = await connection.fetch(
        """
        SELECT c.customer_state AS state, count(*)::int AS orders
        FROM olist.orders o
        JOIN olist.customers c ON c.customer_id = o.customer_id
        WHERE o.order_purchase_timestamp >= $1::date
          AND o.order_purchase_timestamp < ($2::date + interval '1 day')
          AND ($3::text[] IS NULL OR EXISTS (
                SELECT 1 FROM olist.order_items i
                WHERE i.order_id = o.order_id AND i.seller_id = ANY($3::text[])
          ))
        GROUP BY c.customer_state
        ORDER BY orders DESC, state
        LIMIT 200
        """,
        date_from,
        date_to,
        sellers,
    )
    rows = rows_from(records)
    return ToolOutcome(
        summary={"states": len(rows), "orders": sum(row["orders"] for row in rows)},
        rows=rows,
        total_rows=len(rows),
    )
```

It reuses the existing helpers: `seller_filter` applies the tenant's seller scope, `rows_from` converts dates and decimals to JSON values, and `ToolOutcome` carries the summary, rows and total count. The dispatcher already wraps the call in a read-only transaction with the statement timeout and the row cap.

### Step 3: add the MCP wrapper

Add this to `mcp_servers/olist/server.py`, after `top_products`:

```python
@mcp.tool(
    name="orders_by_state@1",
    description=MANIFEST["orders_by_state@1"].description,
    meta=tool_meta("orders_by_state@1"),
)
async def orders_by_state(
    ctx: Context[Any, Any],
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    _check_range(date_from, date_to)
    return await _call_tool(
        ctx,
        "orders_by_state@1",
        {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        lambda connection, tenant: db.orders_by_state(
            connection, tenant, date_from, date_to
        ),
    )
```

`_call_tool` runs the dispatcher: token verification, tenant allowlist, audit insert, span attributes and the tool result event. The wrapper itself only validates the range and builds the arguments.

### Turn it on for the tenant

This is configuration, not code. Add the wire name to `config/tenants.yaml` under `default.tools.allow`:

```yaml
      - orders_by_state@1
```

Without this line the server lists the tool but the tenant allowlist hides it from the model and refuses the call.

### Verify it

The unit test currently asserts exactly five tools. While the experiment is in the tree, add the new name to the expected set:

```python
    assert set(MANIFEST) == {
        "orders_summary@1",
        "orders_late@1",
        "order_lookup@1",
        "top_products@1",
        "describe_schema@1",
        "orders_by_state@1",
    }
```

and change three assertions in `tests/unit/test_mcp_server.py`: `len(tools) == 5` becomes `len(tools) == 6` in `test_server_registers_exactly_the_manifest_tools`, and `len(tenants["default"].allowed_tools) == 5` becomes `== 6` in `test_tenants_carry_allowlists_and_seller_scope`, because the tenant config gained a tool. Rename the first test if you keep the tool for real.

Then rebuild the two services that share the app image and check the manifest count:

```bash
uv run pytest tests/unit/test_mcp_server.py -q
docker compose up -d --build mcp-olist api
curl -s http://localhost:8001/health
```

Expected: the tests pass and `/health` reports `"tools":6`.

For an end-to-end call with a real run token and an audit row, update the integration test in two places. Add the new name to the expected set in `test_lists_the_five_manifest_tools`, and add one line to the `calls` dictionary in `test_calls_every_tool_and_writes_audit_rows`:

```python
            "orders_by_state@1": {"date_from": "2018-03-01", "date_to": "2018-03-31"},
```

and run:

```bash
uv run --env-file .env pytest tests/integration/test_mcp_olist.py -q
```

Expected: the integration tests pass, the audit rows include `orders_by_state` with status `ok` and the tenant from the token. The test asserts that every call returned a summary, rows, a total row count and an audit id.

Finally, ask the chat page a question that needs the new tool, for example "How many orders per state in March 2018?", and confirm `tool.called` shows `orders_by_state@1`. This proves the data agent picked it up through `list_tools` without any change under `src/switchboard` (AC-07b).

### Revert

```bash
git checkout -- mcp_servers/olist/manifest.yaml mcp_servers/olist/db.py \
  mcp_servers/olist/server.py config/tenants.yaml \
  tests/unit/test_mcp_server.py tests/integration/test_mcp_olist.py
docker compose up -d --build mcp-olist api
```

`/health` should report `"tools":5` again. This revert step is why the walkthrough can be run during a demo without committing a real new tool.
