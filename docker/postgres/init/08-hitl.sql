-- Human-in-the-loop approval (T3.01), design.md sections 6.7 and 7.3, ADR 0015.
--
-- Two things live here. First, the append-only note table behind the one demo
-- write tool `add_order_note@1`: the tool may INSERT a note for an order that
-- belongs to the token tenant's sellers, and nothing else. The tool role gets
-- SELECT and INSERT only, so even a compromised server process cannot update or
-- delete a row, and the table holds no order status or business field.
--
-- Second, the run columns the API needs to show and decide one pending approval:
-- `pending_approval` holds the tool name and arguments the graph paused on, and
-- the status check learns `rejected`.
--
-- The note table references olist.orders, which a fresh volume does not have yet
-- (the loader creates it), so that block skips itself with a notice. The loader
-- re-applies this file after loading, and an existing volume with data can run
-- it by hand:
--
--   docker compose exec -T postgres psql -U switchboard -d switchboard \
--     -f /docker-entrypoint-initdb.d/08-hitl.sql
--
-- Every statement is idempotent, so re-applying is safe.

DO $$
BEGIN
    IF to_regclass('olist.orders') IS NULL THEN
        RAISE NOTICE 'olist tables are not loaded yet; skipping the note table';
        RETURN;
    END IF;

    CREATE TABLE IF NOT EXISTS olist.order_notes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        order_id text NOT NULL REFERENCES olist.orders (order_id),
        -- The tenant of the signed run token that added the note, never an
        -- argument of the model.
        tenant_id text NOT NULL,
        note text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT order_notes_note_check CHECK (
            length(btrim(note)) BETWEEN 1 AND 500
        )
    );
    CREATE INDEX IF NOT EXISTS order_notes_tenant_idx
        ON olist.order_notes (tenant_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS order_notes_order_idx
        ON olist.order_notes (order_id);

    COMMENT ON TABLE olist.order_notes IS
        'Append-only notes added by add_order_note@1 behind human approval. No '
        'order status or existing business row is ever changed by that tool.';

    -- Append-only: no UPDATE, no DELETE, no TRUNCATE for anyone but the owner.
    -- The API role gets nothing; the demo reads the table as the loader user.
    REVOKE ALL ON olist.order_notes FROM PUBLIC;
    REVOKE ALL ON olist.order_notes FROM switchboard_app;
    GRANT SELECT, INSERT ON olist.order_notes TO switchboard_tools;
END
$$;

-- The pending tool call of a paused run (T3.01). Kept as jsonb rather than
-- columns because it is exactly the payload the graph paused on, and the API
-- writes it from the orchestrator result, never from a request body.
ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS pending_approval jsonb;

-- `rejected` joins the status set: the user said no, so the run ended without
-- executing the tool. Dropping and recreating the named constraint keeps this
-- file idempotent on both a fresh and an existing volume.
ALTER TABLE public.runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE public.runs
    ADD CONSTRAINT runs_status_check CHECK (
        status IN (
            'running',
            'ok',
            'partial',
            'needs_clarification',
            'timeout',
            'provider_error',
            'auth_error',
            'waiting_approval',
            'rejected'
        )
    );
