"""Switchboard: a traceable multi-agent platform.

The package is a modular monolith. ``switchboard.core`` holds the ports and
domain models and imports nothing outside the standard library and pydantic;
every integration lives in a registered adapter.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("switchboard")
except PackageNotFoundError:  # pragma: no cover - only when run from a checkout
    __version__ = "0.0.0"
