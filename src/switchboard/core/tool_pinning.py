"""Stable fingerprints for MCP tool pinning (V3.10).

Only the fields that define what the model sees are hashed: the tool name,
description and input schema. A version bump changes the wire name and is
already covered by the manifest; metadata that does not affect behavior does
not invalidate an approval.
"""

from __future__ import annotations

import hashlib
import json

from switchboard.core.models import ToolSpec


def tool_fingerprint(spec: ToolSpec) -> str:
    payload = {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["tool_fingerprint"]
