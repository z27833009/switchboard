"""Data agent: a bounded read-only tool loop with bounded result views."""

from __future__ import annotations

import json

from switchboard.agents.base import BaseAgent, partial_result
from switchboard.agents.citations import CitationRegistry
from switchboard.agents.prompts import DATA_INSTRUCTIONS, DATA_SYSTEM
from switchboard.agents.results import (
    ResultPageToolSource,
    ResultStore,
    citation_snippet,
)
from switchboard.core.errors import (
    ToolExecutionError,
    ToolPermissionError,
    ToolValidationError,
)
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    Citation,
    EvidenceRef,
    Message,
    ToolCallRecord,
    ToolSpec,
    Usage,
)
from switchboard.core.ports import AgentDeps, ToolSource


class DataAgent(BaseAgent):
    spec = AgentSpec(
        name="data",
        description="Order, delivery, product and seller questions answered from the Olist tools.",
        cacheable=False,
        needs=["tools"],
    )
    system_prompt = DATA_SYSTEM
    instructions = DATA_INSTRUCTIONS

    async def _collect_tools(
        self, deps: AgentDeps
    ) -> tuple[list[ToolSpec], dict[str, ToolSource], ResultStore]:
        store = ResultStore()
        sources: list[ToolSource] = [*deps.tool_sources, ResultPageToolSource(store)]
        specs: dict[str, ToolSpec] = {}
        owners: dict[str, ToolSource] = {}
        for source in sources:
            for spec in await source.list_tools(deps.ctx):
                specs[spec.wire_name] = spec
                owners[spec.wire_name] = source
        return [specs[name] for name in sorted(specs)], owners, store

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        tools, owners, store = await self._collect_tools(deps)
        messages = self.build_messages(task)
        registry = CitationRegistry()
        evidence: list[EvidenceRef] = []
        records: list[ToolCallRecord] = []
        usage = Usage(input_tokens=0, output_tokens=0)
        last_text = ""

        for _ in range(task.budget.max_tool_steps + 1):
            response = await self.complete(messages, deps, task, tools)
            usage = usage.plus(response.usage)
            last_text = response.text or last_text

            if not response.tool_calls:
                answer = response.text or ""
                return AgentResult(
                    answer=answer,
                    citations=registry.parse(answer),
                    tool_calls=records,
                    evidence=evidence,
                    usage=usage,
                    partial=not evidence and bool(answer),
                )

            messages.append(
                Message(
                    role="assistant",
                    content=response.text or "",
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                source = owners.get(call.name)
                if source is None:
                    messages.append(
                        Message(
                            role="tool",
                            content=json.dumps({"error": f"unknown tool {call.name}"}),
                            tool_call_id=call.call_id,
                        )
                    )
                    records.append(
                        ToolCallRecord(
                            tool=call.name,
                            arguments=call.arguments,
                            status="error",
                            error=f"unknown tool {call.name}",
                        )
                    )
                    continue
                try:
                    result = await source.call(call.name, call.arguments, deps.ctx)
                except ToolPermissionError as exc:
                    messages.append(
                        Message(
                            role="tool",
                            content=json.dumps({"error": f"permission denied: {exc}"}),
                            tool_call_id=call.call_id,
                        )
                    )
                    records.append(
                        ToolCallRecord(
                            tool=call.name,
                            arguments=call.arguments,
                            status="denied",
                            error=str(exc),
                        )
                    )
                    continue
                except (ToolValidationError, ToolExecutionError) as exc:
                    messages.append(
                        Message(
                            role="tool",
                            content=json.dumps({"error": str(exc)}),
                            tool_call_id=call.call_id,
                        )
                    )
                    records.append(
                        ToolCallRecord(
                            tool=call.name,
                            arguments=call.arguments,
                            status="error",
                            error=str(exc),
                        )
                    )
                    continue

                if result.tool == "result_page@1" and result.result_id:
                    result_id = result.result_id
                else:
                    result_id = store.add(result)
                view = result.view(limit=deps.model_view_rows, result_id=result_id)
                ref = f"t{len(evidence) + 1}"
                registry.add(
                    Citation(
                        kind="tool",
                        ref=ref,
                        source_id=result_id,
                        audit_id=result.audit_id,
                        snippet=citation_snippet(result, view),
                    )
                )
                evidence.append(EvidenceRef(kind="tool", ref=ref, source_id=result_id))
                messages.append(
                    Message(
                        role="tool",
                        content=(
                            f'<tool_result ref="{ref}" tool="{call.name}" '
                            f'result_id="{result_id}">\n'
                            f"{view.model_dump_json()}\n"
                            "</tool_result>"
                        ),
                        tool_call_id=call.call_id,
                        result_id=result_id,
                        total_rows=view.total_rows,
                    )
                )
                records.append(
                    ToolCallRecord(
                        tool=call.name,
                        arguments=call.arguments,
                        status="ok",
                        result_id=result_id,
                        audit_id=result.audit_id,
                        duration_ms=result.duration_ms,
                    )
                )

        return partial_result(
            last_text or "I reached the tool step limit before I could answer the question."
        ).model_copy(
            update={
                "tool_calls": records,
                "evidence": evidence,
                "usage": usage,
                "citations": registry.parse(last_text),
            }
        )
