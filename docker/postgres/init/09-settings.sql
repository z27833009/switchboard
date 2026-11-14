-- Runtime settings control plane (V3.01).
--
-- The four YAML files remain the seed and export format. At runtime the API
-- reads these documents from PostgreSQL, validates them with the same pydantic
-- models as before, and records every seed or replacement as a revision plus an
-- audit row. A later task adds the editable API and UI; this migration only
-- makes the database the source of truth.

CREATE TABLE IF NOT EXISTS public.settings_documents (
    kind text PRIMARY KEY,
    payload jsonb NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL,
    CONSTRAINT settings_documents_kind_check
        CHECK (kind IN ('platform', 'providers', 'tenants', 'prices'))
);

CREATE TABLE IF NOT EXISTS public.settings_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    payload jsonb NOT NULL,
    actor text NOT NULL,
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT settings_revisions_kind_check
        CHECK (kind IN ('platform', 'providers', 'tenants', 'prices')),
    CONSTRAINT settings_revisions_unique_revision UNIQUE (kind, revision)
);

CREATE TABLE IF NOT EXISTS public.config_audit (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    action text NOT NULL,
    actor text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT config_audit_kind_check
        CHECK (kind IN ('platform', 'providers', 'tenants', 'prices'))
);
CREATE INDEX IF NOT EXISTS config_audit_created_idx
    ON public.config_audit (created_at DESC);

-- The API reads every document and appends revisions and audit rows. No tool
-- role can read or write the control plane.
GRANT SELECT, INSERT, UPDATE ON public.settings_documents TO switchboard_app;
GRANT SELECT, INSERT ON public.settings_revisions TO switchboard_app;
GRANT SELECT, INSERT ON public.config_audit TO switchboard_app;
