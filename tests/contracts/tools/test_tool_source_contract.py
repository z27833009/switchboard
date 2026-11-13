"""ToolSource contract for the in-process platform tools.

``McpToolSource`` has its own contract module against the running server
(``test_mcp_source.py``); these cases cover the second family of tool sources,
the platform tools that run in this process: ``search_docs@1`` and
``result_page@1``. They share the port assertions (a read-only spec, a result
with rows and a version, an unmapped name answered with a tool error and a bad
argument answered with a validation error) and add the audit expectation of
each source: a paged result keeps the audit id of the result it pages, while a
document search is not audited and reports no id.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import pytest
from tests.contracts.support import InMemoryRetriever, chunk, run_context

from switchboard.adapters.tools.platform import SearchDocsToolSource
from switchboard.agents.results import ResultPageToolSource, ResultStore
from switchboard.core.errors import ToolExecutionError, ToolValidationError
from switchboard.core.models import RunContext, ToolResult, ToolSpec
from switchboard.core.ports import ToolSource
from switchboard.registry import TOOL_SOURCES, Registry


@dataclass(frozen=True)
class ToolContract:
    label: str
    source: ToolSource
    ctx: RunContext
    tool_name: str
    valid_args: dict[str, Any]
    expected_audit_id: str | None


def build_search_contract() -> ToolContract:
    source = SearchDocsToolSource(InMemoryRetriever([chunk(0), chunk(1)]))
    return ToolContract(
        label="search_docs",
        source=source,
        ctx=run_context(allowed_tools={"search_docs@1"}),
        tool_name="search_docs@1",
        valid_args={"query": "return window", "k": 2},
        expected_audit_id=None,
    )


def build_result_page_contract() -> ToolContract:
    store = ResultStore()
    result = ToolResult(
        tool="orders_late@1",
        version="1",
        total_rows=3,
        summary={"late_orders": 3},
        rows=[{"order_id": "o1"}, {"order_id": "o2"}, {"order_id": "o3"}],
        audit_id="audit-r1",
    )
    result_id = store.add(result)
    return ToolContract(
        label="result_page",
        source=ResultPageToolSource(store),
        ctx=run_context(allowed_tools={"result_page@1"}),
        tool_name="result_page@1",
        valid_args={"result_id": result_id, "offset": 1, "limit": 1},
        expected_audit_id="audit-r1",
    )


BUILDERS = (build_search_contract, build_result_page_contract)


@pytest.fixture(params=BUILDERS, ids=["search_docs", "result_page"])
def contract(request: pytest.FixtureRequest) -> ToolContract:
    builder = cast(Callable[[], ToolContract], request.param)
    return builder()


def test_registered_tool_source_adapter_has_a_contract_module() -> None:
    assert Registry().names(TOOL_SOURCES) == ["mcp"]


async def test_list_tools_returns_read_specs(contract: ToolContract) -> None:
    specs = await contract.source.list_tools(contract.ctx)
    assert specs
    assert all(isinstance(spec, ToolSpec) for spec in specs)
    assert all(spec.risk == "read" for spec in specs)
    assert len({spec.wire_name for spec in specs}) == len(specs)
    assert any(spec.wire_name == contract.tool_name for spec in specs)
    assert all(isinstance(spec.input_schema, dict) for spec in specs)


async def test_a_call_returns_a_tool_result(contract: ToolContract) -> None:
    specs = {spec.wire_name: spec for spec in await contract.source.list_tools(contract.ctx)}
    result = await contract.source.call(contract.tool_name, contract.valid_args, contract.ctx)
    assert isinstance(result, ToolResult)
    assert result.tool == contract.tool_name
    assert result.version == specs[contract.tool_name].version
    assert result.total_rows >= len(result.rows)
    assert result.total_rows >= 0
    assert result.duration_ms >= 0
    assert isinstance(result.rows, list)
    assert result.summary is None or isinstance(result.summary, dict)


async def test_the_audit_id_follows_the_source_contract(contract: ToolContract) -> None:
    result = await contract.source.call(contract.tool_name, contract.valid_args, contract.ctx)
    assert result.audit_id == contract.expected_audit_id
    if contract.label == "result_page":
        # The paged result keeps the id of the tool call that fetched it.
        assert result.audit_id is not None
    else:
        # Platform tools are not audited; the MCP source is (T1.07).
        assert result.audit_id is None


async def test_an_unknown_tool_is_a_tool_execution_error(contract: ToolContract) -> None:
    with pytest.raises(ToolExecutionError):
        await contract.source.call("missing@1", {}, contract.ctx)


async def test_a_bad_argument_is_a_tool_validation_error(contract: ToolContract) -> None:
    with pytest.raises(ToolValidationError):
        await contract.source.call(contract.tool_name, {}, contract.ctx)


def test_the_mcp_source_is_the_registered_adapter() -> None:
    from switchboard.adapters.tools.mcp_client import McpToolSource

    assert Registry().load(TOOL_SOURCES, "mcp") is McpToolSource


async def test_a_paged_result_keeps_the_source_rows_and_audit_id() -> None:
    store = ResultStore()
    result_id = store.add(
        ToolResult(
            tool="orders_late@1",
            version="1",
            total_rows=3,
            rows=[{"order_id": "o1"}, {"order_id": "o2"}, {"order_id": "o3"}],
            audit_id=str(uuid.uuid4()),
        )
    )
    source = ResultPageToolSource(store)
    page = await source.call(
        "result_page@1",
        {"result_id": result_id, "offset": 1, "limit": 1},
        run_context(),
    )
    assert page.rows == [{"order_id": "o2"}]
    assert page.total_rows == 3
    assert page.result_id == result_id
    uuid.UUID(str(page.audit_id))
