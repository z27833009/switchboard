"""Supervisor prompt and plan schema shared by the orchestrator and the eval suite.

The routing suite must score the same request the production supervisor sends,
so both sides import these helpers instead of keeping a copy. The module uses
only core models (design.md section 6.3).
"""

from __future__ import annotations

from typing import Any

from switchboard.core.models import AgentSpec

SUPERVISOR_SYSTEM = (
    "You are Switchboard's routing supervisor. Choose the smallest set of "
    "registered agents that can answer the user's question. direct handles small "
    "talk only. docs answers policy questions from the document index. data "
    "answers order and product questions from read-only tools. Return a JSON "
    "object with an agents array and a one-sentence reason. Do not invent agent "
    "names and do not add an agent that is not needed."
)


def plan_schema(names: list[str], max_items: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "agents": {
                "type": "array",
                "items": {"type": "string", "enum": names},
                "minItems": 1,
                "maxItems": max_items,
            },
            "reason": {"type": "string"},
        },
        "required": ["agents", "reason"],
    }


def supervisor_prompt(available: list[AgentSpec], query: str) -> str:
    lines = ["Available agents:"]
    lines.extend(f"- {spec.name}: {spec.description}" for spec in available)
    lines.append(
        "Choose one or more agents that can answer the question. Return a JSON "
        "object with an agents array and a reason."
    )
    lines.append(f"Question: {query}")
    return "\n".join(lines)


__all__ = ["SUPERVISOR_SYSTEM", "plan_schema", "supervisor_prompt"]
