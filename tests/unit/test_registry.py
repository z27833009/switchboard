"""Entry point resolution for agents and other registered components."""

from dataclasses import dataclass
from typing import Any

import pytest

from switchboard.core.errors import RegistryError, UnknownComponentError
from switchboard.core.models import AgentSpec
from switchboard.registry import AGENTS, PROVIDERS, Registry


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class FakeAgent:
    spec = AgentSpec(name="direct", description="small talk", cacheable=True)


class FakeProvider:
    name = "fake"


def test_resolves_an_entry_point_by_group_and_name() -> None:
    registry = Registry([FakeEntryPoint("direct", AGENTS, FakeAgent)])
    assert registry.names(AGENTS) == ["direct"]
    assert registry.load(AGENTS, "direct") is FakeAgent
    assert registry.agents() == {"direct": FakeAgent}


def test_lists_names_sorted_without_loading() -> None:
    registry = Registry(
        [
            FakeEntryPoint("docs", AGENTS, FakeAgent),
            FakeEntryPoint("direct", AGENTS, FakeAgent),
            FakeEntryPoint("fake", PROVIDERS, FakeProvider),
        ]
    )
    assert registry.names(AGENTS) == ["direct", "docs"]
    assert registry.names("switchboard.unknown") == []


def test_unknown_component_names_the_group_and_available_names() -> None:
    registry = Registry([FakeEntryPoint("direct", AGENTS, FakeAgent)])
    with pytest.raises(UnknownComponentError, match="direct"):
        registry.load(AGENTS, "nope")


def test_duplicate_entry_point_is_rejected() -> None:
    with pytest.raises(RegistryError, match="duplicate"):
        Registry(
            [
                FakeEntryPoint("direct", AGENTS, FakeAgent),
                FakeEntryPoint("direct", AGENTS, FakeAgent),
            ]
        )
