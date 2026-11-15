-- V3.07: who changed a conversation and what they did.
--
-- Runs, traces and tool audit rows stay untouched when a conversation is
-- renamed, hidden, restored or bulk hidden. This table records the action
-- separately so an administrator can see it after the sidebar has hidden the
-- conversation.

CREATE TABLE IF NOT EXISTS public.session_audit (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL REFERENCES public.tenants (id) ON DELETE CASCADE,
    session_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('rename', 'delete', 'restore', 'bulk_delete')),
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS session_audit_tenant_created_idx
    ON public.session_audit (tenant_id, created_at DESC);

GRANT SELECT, INSERT ON public.session_audit TO switchboard_app;
