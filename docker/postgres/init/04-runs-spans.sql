-- Runs and spans (T1.12), design.md sections 12.2 and 14.
--
-- Applied automatically only on a fresh volume. Existing volumes run this file
-- with `docker compose exec -T postgres psql -U switchboard -d switchboard`.
-- The statements are idempotent so re-applying is safe.

CREATE TABLE IF NOT EXISTS public.runs (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES public.tenants (id),
    session_id text,
    query text NOT NULL,
    orchestrator text NOT NULL,
    plan text,
    answer text,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL,
    cache_hit boolean NOT NULL DEFAULT false,
    attempts integer NOT NULL DEFAULT 0,
    input_tokens integer NOT NULL DEFAULT 0,
    cached_input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_usd numeric,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    trace_complete boolean NOT NULL DEFAULT true,
    CONSTRAINT runs_status_check CHECK (
        status IN (
            'running',
            'ok',
            'partial',
            'needs_clarification',
            'timeout',
            'provider_error',
            'auth_error',
            'waiting_approval'
        )
    ),
    CONSTRAINT runs_counts_check CHECK (
        attempts >= 0
        AND input_tokens >= 0
        AND cached_input_tokens >= 0
        AND output_tokens >= 0
    )
);
CREATE INDEX IF NOT EXISTS runs_tenant_started_idx
    ON public.runs (tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS runs_status_idx ON public.runs (status);

-- Added in T1.13: false when the API's span flush timed out before it answered,
-- so GET /runs/{id}/trace can mark the tree incomplete (design 12.2). Errors are
-- persisted with the run so a failed run explains itself in the run list.
ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS trace_complete boolean NOT NULL DEFAULT true;
ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS errors jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Added in T2.06: the provider the tenant is configured with, so GET /runs can
-- aggregate cost per provider (design 12.4). Rows written before this column
-- existed keep null and are grouped as "unknown".
ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS provider text;

-- The run row is inserted before any span can reference it (design 12.2). The
-- span table is append-only: no UPDATE or DELETE grant for any role.
CREATE TABLE IF NOT EXISTS public.spans (
    span_id text PRIMARY KEY,
    trace_id text NOT NULL,
    parent_span_id text,
    run_id uuid,
    name text NOT NULL,
    kind text NOT NULL,
    start_time timestamptz NOT NULL,
    end_time timestamptz NOT NULL,
    status text NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    events jsonb NOT NULL DEFAULT '[]'::jsonb
);
-- No foreign key on run_id on purpose: spans from the MCP process must never
-- fail because the app has not committed the run row yet.
CREATE INDEX IF NOT EXISTS spans_trace_idx ON public.spans (trace_id);
CREATE INDEX IF NOT EXISTS spans_run_idx ON public.spans (run_id);
CREATE INDEX IF NOT EXISTS spans_run_start_idx ON public.spans (run_id, start_time);

-- The API role starts and finishes runs and reads their spans.
GRANT SELECT, INSERT, UPDATE ON public.runs TO switchboard_app;
GRANT SELECT, INSERT ON public.spans TO switchboard_app;

-- The tool server writes its own spans and keeps its audit INSERT. It cannot
-- read the trace back, so a compromised tool server cannot enumerate runs.
GRANT INSERT ON public.spans TO switchboard_tools;
