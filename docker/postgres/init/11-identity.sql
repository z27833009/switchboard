-- V3.02: users and per-tenant memberships.
--
-- This slice stores the directory and the role each user has in a tenant. It
-- does not add sign-in: the API still has no session or password, so the local
-- administrator is recorded for audit and the UI can manage members before the
-- authentication slice lands.

CREATE TABLE IF NOT EXISTS public.users (
    id text PRIMARY KEY,
    email text NOT NULL UNIQUE,
    display_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.memberships (
    tenant_id text NOT NULL REFERENCES public.tenants (id) ON DELETE CASCADE,
    user_id text NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('admin', 'member')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS memberships_user_idx ON public.memberships (user_id);

GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.users, public.memberships
    TO switchboard_app;

INSERT INTO public.users (id, email, display_name)
VALUES ('local-admin', 'admin@local.invalid', 'Local administrator')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.memberships (tenant_id, user_id, role)
VALUES ('default', 'local-admin', 'admin')
ON CONFLICT (tenant_id, user_id) DO NOTHING;

INSERT INTO public.memberships (tenant_id, user_id, role)
VALUES ('seller_a', 'local-admin', 'member')
ON CONFLICT (tenant_id, user_id) DO NOTHING;
