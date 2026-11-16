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

CREATE TABLE IF NOT EXISTS public.control_mcp_tools (
    server text NOT NULL REFERENCES public.control_mcp_servers(name) ON DELETE CASCADE,
    wire_name text NOT NULL,
    name text NOT NULL,
    version text NOT NULL,
    description text NOT NULL,
    risk text NOT NULL CHECK (risk IN ('read', 'write')),
    tenant_scoped boolean NOT NULL DEFAULT false,
    input_schema jsonb NOT NULL,
    sensitive_args jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (server, wire_name)
);

CREATE TABLE IF NOT EXISTS public.control_agent_versions (
    agent text NOT NULL REFERENCES public.control_agents(name) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version >= 1),
    spec jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent, version)
);

CREATE TABLE IF NOT EXISTS public.control_tenant_bindings (
    tenant_id text PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
    provider text NOT NULL,
    model text,
    agents jsonb NOT NULL,
    tools_allow jsonb NOT NULL,
    tools_ask jsonb NOT NULL,
    connectors jsonb NOT NULL,
    retrieval jsonb NOT NULL,
    cache jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.control_providers, public.control_models,
       public.control_mcp_servers, public.control_mcp_tools,
       public.control_agents, public.control_agent_versions,
       public.control_tenant_bindings
    TO switchboard_app;
