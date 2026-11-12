"""Exporter selection from ``platform.yaml`` (design.md section 12.2).

The API and the MCP server are separate processes but must make the same
export decision, so both read the ``tracing`` block of ``platform.yaml``
through this module instead of each reading an environment variable. The
committed file uses ``${SWITCHBOARD_TRACING_EXPORTER:-postgres}``, which lets
the tracing compose profile switch both processes with one variable without
editing a tracked file.

Only ``OTEL_EXPORTER_OTLP_ENDPOINT`` is read from the environment: it is the
standard OpenTelemetry variable, and the compose file points it at the Jaeger
container for the tracing profile. A missing endpoint is a named startup error
rather than a silent fallback, because an OTLP exporter with no destination
would drop every span.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from switchboard.core.config import substitute_env
from switchboard.core.errors import ConfigError

DEFAULT_EXPORTER = "postgres"
SUPPORTED_EXPORTERS = ("none", "postgres", "otlp")
OTLP_ENDPOINT_VARIABLE = "OTEL_EXPORTER_OTLP_ENDPOINT"


@dataclass(frozen=True)
class TracingOptions:
    """The exporter a process should build and its optional OTLP destination."""

    exporter: str = DEFAULT_EXPORTER
    # Raw value of OTEL_EXPORTER_OTLP_ENDPOINT. The setup module normalizes it
    # to the traces path, so a base URL and a full /v1/traces URL both work.
    otlp_endpoint: str | None = None


def resolve_tracing_options(
    platform: Mapping[str, Any], env: Mapping[str, str] | None = None
) -> TracingOptions:
    """Validate a parsed ``platform.yaml`` mapping and its environment."""
    source = dict(os.environ) if env is None else dict(env)
    tracing = platform.get("tracing") or {}
    if not isinstance(tracing, Mapping):
        raise ConfigError("platform.yaml: tracing must be a mapping")
    raw = tracing.get("exporter", DEFAULT_EXPORTER)
    if not isinstance(raw, str):
        raise ConfigError("platform.yaml: tracing.exporter must be a string")
    exporter = substitute_env(raw, source)
    if exporter not in SUPPORTED_EXPORTERS:
        allowed = ", ".join(SUPPORTED_EXPORTERS)
        raise ConfigError(
            f"platform.yaml: unknown tracing exporter {exporter!r}; allowed: {allowed}"
        )
    endpoint = source.get(OTLP_ENDPOINT_VARIABLE, "").strip() or None
    if exporter == "otlp" and endpoint is None:
        raise ConfigError(
            f"platform.yaml: tracing.exporter is 'otlp' but {OTLP_ENDPOINT_VARIABLE} is not set"
        )
    return TracingOptions(
        exporter=exporter,
        otlp_endpoint=endpoint if exporter == "otlp" else None,
    )


def load_tracing_options(path: Path, env: Mapping[str, str] | None = None) -> TracingOptions:
    """Read the ``tracing`` block of one ``platform.yaml`` file."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return resolve_tracing_options(payload, env)


__all__ = [
    "DEFAULT_EXPORTER",
    "OTLP_ENDPOINT_VARIABLE",
    "SUPPORTED_EXPORTERS",
    "TracingOptions",
    "load_tracing_options",
    "resolve_tracing_options",
]
