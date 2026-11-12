"""Deterministic merge of parallel agent answers (design 6.5)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from switchboard.agents.base import zero_usage
from switchboard.core.models import AgentResult, Usage
from switchboard.core.pricing import add_cost


class MergeAgent:
    """Combine answers, keep every citation and state what is missing.

    It is not routable and makes no model call: the merge sees answers and
    citations, never raw evidence (context isolation by construction).
    """

    def merge(
        self,
        results: Mapping[str, AgentResult],
        missing: Iterable[str] = (),
    ) -> AgentResult:
        missing_list = sorted(missing)
        if not results:
            return AgentResult(
                answer="No agent produced an answer for this question.",
                usage=zero_usage(),
                partial=True,
            )
        if len(results) == 1 and not missing_list:
            return next(iter(results.values()))

        parts: list[str] = []
        citations = []
        tool_calls = []
        evidence = []
        usage = Usage(input_tokens=0, output_tokens=0)
        cost: float | None = None
        partial = False
        for name in sorted(results):
            result = results[name]
            if result.answer.strip():
                parts.append(f"{name}: {result.answer.strip()}")
            citations.extend(result.citations)
            tool_calls.extend(result.tool_calls)
            evidence.extend(result.evidence)
            usage = usage.plus(result.usage)
            cost = add_cost(cost, result.cost_usd)
            partial = partial or result.partial
        if missing_list:
            parts.append(
                "Missing source: " + ", ".join(missing_list) + ". The answer above is partial."
            )
            partial = True
        return AgentResult(
            answer="\n\n".join(parts),
            citations=citations,
            tool_calls=tool_calls,
            evidence=evidence,
            usage=usage,
            cost_usd=cost,
            partial=partial,
        )
