-- V3.01/V3.03: provider API keys encrypted at rest.
--
-- The ciphertext is a Fernet token produced by the API with
-- SWITCHBOARD_SETTINGS_KEY. The database never sees the key. A credential is
-- read at startup and merged into the in-memory provider config; GET /settings
-- never returns it.

CREATE TABLE IF NOT EXISTS public.provider_credentials (
    provider_name text PRIMARY KEY,
    ciphertext bytea NOT NULL,
    key_version integer NOT NULL DEFAULT 1 CHECK (key_version >= 1),
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL
);

GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.provider_credentials
    TO switchboard_app;
