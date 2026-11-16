-- V3.10: pin every approved MCP tool by a fingerprint of its name,
-- description and input schema. A changed or new tool keeps its current
-- fingerprint but no approved fingerprint, so the API hides it until an admin
-- approves the source again.

ALTER TABLE public.control_mcp_tools
    ADD COLUMN IF NOT EXISTS fingerprint text,
    ADD COLUMN IF NOT EXISTS approved_fingerprint text;
