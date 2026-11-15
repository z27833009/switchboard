-- V3.02: local sign-in fields.
--
-- password_hash is null until the first successful login with the bootstrap
-- password from SWITCHBOARD_ADMIN_PASSWORD, or until an admin sets one. The
-- hash format is pbkdf2_sha256$iterations$salt$digest and never leaves the API.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS password_hash text;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
