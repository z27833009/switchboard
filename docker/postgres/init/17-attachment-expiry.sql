-- V3.06: attachments are request-scoped and expire.
--
-- The run keeps the attachment names and text so a reopened conversation can
-- show what was attached. A startup cleanup clears rows past this timestamp.

ALTER TABLE public.runs
    ADD COLUMN IF NOT EXISTS attachments_expires_at timestamptz;
