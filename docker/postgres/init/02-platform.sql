-- Platform tables needed before T1.12 adds runs and spans.
-- Applied automatically only on a fresh volume; existing volumes run this file
-- with `docker compose exec -T postgres psql -U switchboard -d switchboard`.

CREATE TABLE IF NOT EXISTS public.tool_audit (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid,
    tenant_id text NOT NULL,
    tool_name text NOT NULL,
    tool_version text NOT NULL,
    args_hash text NOT NULL,
    args_redacted jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL,
    duration_ms integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tool_audit_status_check CHECK (status IN ('ok', 'error', 'denied')),
    CONSTRAINT tool_audit_duration_check CHECK (duration_ms >= 0)
);
CREATE INDEX IF NOT EXISTS tool_audit_run_idx ON public.tool_audit (run_id);
CREATE INDEX IF NOT EXISTS tool_audit_tenant_idx ON public.tool_audit (tenant_id, created_at DESC);

-- The tool role appends audit rows and can never change or delete them.
GRANT INSERT ON public.tool_audit TO switchboard_tools;
GRANT SELECT ON public.tool_audit TO switchboard_app;
