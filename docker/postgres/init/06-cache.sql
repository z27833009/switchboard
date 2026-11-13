-- Semantic cache entries (T2.18), design.md sections 10 and 14.
--
-- Applied automatically only on a fresh volume. Existing volumes run this file
-- with `docker compose exec -T postgres psql -U switchboard -d switchboard
-- -f /docker-entrypoint-initdb.d/06-cache.sql`. The statements are idempotent so
-- re-applying is safe.

CREATE TABLE IF NOT EXISTS public.cache_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL REFERENCES public.tenants (id) ON DELETE CASCADE,
    -- The plan label that produced the answer, for example "docs". It lets a
    -- hit say which plan it came from without a second model call (design 10.2).
    plan text NOT NULL,
    -- The normalized query is stored for inspection and an exact-match fast
    -- path; matching itself uses the embedding.
    query_text text NOT NULL,
    embedding vector(1024) NOT NULL,
    -- The serialized AgentResult, so a hit returns the answer, citations and
    -- evidence without a model call.
    answer jsonb NOT NULL,
    -- The tenant's document index version when the entry was written. Ingestion
    -- bumps that version and every older entry stops matching (design 10.3).
    index_version integer NOT NULL,
    hits integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CONSTRAINT cache_entries_hits_check CHECK (hits >= 0)
);

-- Cosine distance search, the same operator family the chunk index uses. The
-- entry count is small, but the HNSW index keeps the lookup a single ordered
-- scan and matches the retrieval path a reviewer already knows.
CREATE INDEX IF NOT EXISTS cache_entries_embedding_idx
    ON public.cache_entries USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS cache_entries_tenant_version_idx
    ON public.cache_entries (tenant_id, index_version);
CREATE INDEX IF NOT EXISTS cache_entries_expires_idx
    ON public.cache_entries (expires_at);

-- The application role reads, writes and prunes its own cache rows. A nightly
-- job is out of scope for the demo, so the adapter deletes expired and older
-- index-version rows on the next store.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.cache_entries TO switchboard_app;

-- The tool server never touches the cache.
REVOKE ALL ON public.cache_entries FROM switchboard_tools;
