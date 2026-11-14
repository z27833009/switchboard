-- Conversation metadata (V3.07): a title override and a soft-delete marker.
-- Runs stay immutable and keep their session_id, so deleting a conversation
-- from the sidebar never deletes its runs, traces or audit rows.

CREATE TABLE IF NOT EXISTS public.session_metadata (
    tenant_id text NOT NULL REFERENCES public.tenants (id),
    session_id text NOT NULL,
    title text,
    deleted_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, session_id)
);

CREATE INDEX IF NOT EXISTS session_metadata_tenant_idx
    ON public.session_metadata (tenant_id, updated_at DESC);

GRANT SELECT, INSERT, UPDATE ON public.session_metadata TO switchboard_app;
