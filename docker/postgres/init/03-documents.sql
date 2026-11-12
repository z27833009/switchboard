-- Documents, chunks and the tenant document index version (T1.09).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS public.tenants (
    id text PRIMARY KEY,
    name text NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    doc_index_version integer NOT NULL DEFAULT 0
);

INSERT INTO public.tenants (id, name) VALUES
    ('default', 'Default tenant'),
    ('seller_a', 'Seller A')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    source text NOT NULL,
    title text NOT NULL,
    content_hash text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source)
);

CREATE TABLE IF NOT EXISTS public.chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES public.documents (id) ON DELETE CASCADE,
    tenant_id text NOT NULL,
    ordinal integer NOT NULL,
    text text NOT NULL,
    embedding vector(1024) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS chunks_tenant_idx ON public.chunks (tenant_id);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON public.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON public.chunks USING gin (tsv);

GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.tenants, public.documents, public.chunks
    TO switchboard_app;
