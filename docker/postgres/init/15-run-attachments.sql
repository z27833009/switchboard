-- V3.06: keep the attachment names and text with the run so a reopened
-- conversation shows what was attached. The API returns only the stored shape.

ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS attachments jsonb NOT NULL DEFAULT '[]'::jsonb;
