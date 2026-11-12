"""YAML loading and the aggregate settings object.

PyYAML is used here and not in ``core/config.py``, so the import-linter rule
that core depends only on the standard library and pydantic stays true.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from switchboard.core.config import (
    PlatformConfig,
    ProvidersConfig,
    Settings,
    TenantsConfig,
    substitute_env,
)
from switchboard.core.errors import ConfigError


def read_yaml(path: Path) -> Any:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {} if payload is None else payload


def load_model[ModelT: BaseModel](
    path: Path, model: type[ModelT], env: Mapping[str, str]
) -> ModelT:
    try:
        return model.model_validate(substitute_env(read_yaml(path), env))
    except (yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_settings(config_dir: Path, env: Mapping[str, str] | None = None) -> Settings:
    """Read platform.yaml, providers.yaml and tenants.yaml, then validate them."""
    source = dict(os.environ) if env is None else dict(env)
    platform = load_model(config_dir / "platform.yaml", PlatformConfig, source)
    providers = load_model(config_dir / "providers.yaml", ProvidersConfig, source)
    tenants = load_model(config_dir / "tenants.yaml", TenantsConfig, source)
    try:
        return Settings(platform=platform, providers=providers, tenants=tenants)
    except ValueError as exc:
        raise ConfigError(f"{config_dir}: {exc}") from exc
