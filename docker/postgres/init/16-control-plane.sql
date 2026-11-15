-- V3.01: normalized control-plane read model.
--
-- The settings documents remain the validated write path. These tables are the
-- queryable projection used by later control-plane screens and joins. They are
-- rebuilt in one transaction at startup and after POST /settings/apply.

CREATE TABLE IF NOT EXISTS public.control_providers (
    name text PRIMARY KEY,
    adapter text NOT NULL,
    base_url text,
    model text,
    capabilities jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.control_models (
    provider text NOT NULL,
    model text NOT NULL,
    capabilities jsonb,
    price jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, model)
);

CREATE TABLE IF NOT EXISTS public.control_mcp_servers (
    name text PRIMARY KEY,
    adapter text NOT NULL,
    url text,
    manifest text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.control_agents (
    name text PRIMARY KEY,
    description text NOT NULL,
    cacheable boolean NOT NULL,
    exclusive boolean NOT NULL DEFAULT false,
    needs jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.control_providers, public.control_models,
       public.control_mcp_servers, public.control_agents
    TO switchboard_app;
