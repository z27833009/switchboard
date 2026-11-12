-- Feedback on runs (T2.10), design.md sections 14 and 20.1.
--
-- Applied automatically only on a fresh volume. Existing volumes run this file
-- with `docker compose exec -T postgres psql -U switchboard -d switchboard
-- -f /docker-entrypoint-initdb.d/05-feedback.sql`. The statements are
-- idempotent so re-applying is safe.

CREATE TABLE IF NOT EXISTS public.feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    tenant_id text NOT NULL REFERENCES public.tenants (id),
    -- -1 is a negative rating, 1 is positive. Zero and every other value is
    -- rejected by the API and by this check.
    rating smallint NOT NULL,
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT feedback_rating_check CHECK (rating IN (-1, 1)),
    CONSTRAINT feedback_note_length_check CHECK (note IS NULL OR char_length(note) <= 2000)
);

-- One run's feedback, newest first (GET /runs/{id} shows the latest row), and
-- one tenant's feedback over time for `switchboard eval harvest`.
CREATE INDEX IF NOT EXISTS feedback_run_created_idx
    ON public.feedback (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_tenant_created_idx
    ON public.feedback (tenant_id, created_at DESC);

-- A feedback row can only point at a run of the same tenant, enforced by the
-- database and not only by the route (design.md sections 11 and 14). The
-- composite foreign key needs a unique key on the parent pair; `id` is already
-- unique, so this index adds no new values, only the pair the FK can use.
CREATE UNIQUE INDEX IF NOT EXISTS runs_id_tenant_uidx
    ON public.runs (id, tenant_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'feedback_run_tenant_fkey'
          AND conrelid = 'public.feedback'::regclass
    ) THEN
        ALTER TABLE public.feedback
            ADD CONSTRAINT feedback_run_tenant_fkey
            FOREIGN KEY (run_id, tenant_id)
            REFERENCES public.runs (id, tenant_id);
    END IF;
END
$$;

-- Feedback is append-only: the application role may add a row and read rows,
-- never change or delete one. A rating is a fact about a run at a time; the
-- newest row is the run's current verdict, so a change of mind is a new row.
GRANT SELECT, INSERT ON public.feedback TO switchboard_app;

-- The tool role has no business with feedback.
REVOKE ALL ON public.feedback FROM switchboard_tools;
