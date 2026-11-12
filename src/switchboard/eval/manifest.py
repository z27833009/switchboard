"""Read the Olist tool manifest into core ``ToolSpec`` objects.

The MCP server owns the same file but deliberately does not import
``switchboard.core`` (design.md section 3). The eval harness reads the manifest
directly so the toolcall suite sends exactly the specs the server publishes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from switchboard.core.errors import ConfigError
from switchboard.core.models import ToolSpec

DEFAULT_TOOL_MANIFEST = Path("mcp_servers/olist/manifest.yaml")


def load_tool_specs(path: Path = DEFAULT_TOOL_MANIFEST) -> list[ToolSpec]:
    try:
        payload: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read tool manifest {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"tool manifest {path} is not valid YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"tool manifest {path} must contain a mapping")
    entries = payload.get("tools")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"tool manifest {path} has no tools list")

    specs: list[ToolSpec] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError(f"tool manifest {path} has a non-mapping tool entry")
        try:
            spec = ToolSpec.model_validate(entry)
        except ValidationError as exc:
            raise ConfigError(f"tool manifest {path} has an invalid tool: {exc}") from exc
        if spec.wire_name in seen:
            raise ConfigError(f"tool manifest {path} declares {spec.wire_name} twice")
        seen.add(spec.wire_name)
        specs.append(spec)
    return sorted(specs, key=lambda spec: spec.wire_name)


__all__ = ["DEFAULT_TOOL_MANIFEST", "load_tool_specs"]
