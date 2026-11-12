"""Supervisor prompt and plan schema shared by the orchestrator and the eval suite.

The routing suite must score the same request the production supervisor sends,
so both sides import these helpers instead of keeping a copy. The module uses
only core models (design.md section 6.3).
"""

from __future__ import annotations

from typing import Any

from switchboard.core.models import AgentSpec

SUPERVISOR_SYSTEM = (
    "You are Switchboard's routing supervisor. Choose every registered agent "
    "whose evidence the question needs, and only those; a question may need more "
    "than one agent. direct answers small talk and questions about the assistant "
    "only, and is never combined with another agent. docs answers policy rules "
    "from the document index: returns, refunds, shipping, payments and "
    "installments, late-delivery compensation, and seller onboarding. data "
    "answers record and aggregate questions from the read-only tools: specific "
    "orders, customers, products and sellers, counts, dates, rankings and "
    "totals. When a question asks for a record or a number and also for the "
    "policy that applies to it, choose both docs and data. Return a JSON object "
    "with an agents array and a one-sentence reason. Do not invent agent names."
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
        "Choose every agent whose evidence the question needs, and only those. "
        "Return a JSON object with an agents array and a reason."
    )
    lines.append(f"Question: {query}")
    return "\n".join(lines)


__all__ = ["SUPERVISOR_SYSTEM", "plan_schema", "supervisor_prompt"]
