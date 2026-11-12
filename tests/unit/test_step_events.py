"""StepEventProcessor turns span starts and ends into the design 12.5 events."""

from __future__ import annotations

import asyncio

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import SpanKind

from switchboard.api.step_events import (
    AGENT_FINISHED,
    AGENT_STARTED,
    ANSWER_DELTA,
    GATE_CHECKED,
    PLAN_DECIDED,
    RUN_STARTED,
    TOOL_CALLED,
    StepEvent,
    StepEventProcessor,
    StepEventPublisher,
)
from switchboard.tracing.spans import agent_span, run_span, set_attributes, supervisor_span

RUN_ID = "6f1c2a54-0000-4000-8000-000000000001"


def drain(queue: asyncio.Queue[StepEvent]) -> list[StepEvent]:
    events: list[StepEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def test_processor_publishes_the_run_plan_agent_tool_and_gate_events() -> None:
    publisher = StepEventPublisher()
    provider = TracerProvider()
    provider.add_span_processor(StepEventProcessor(publisher))
    queue = publisher.register(RUN_ID)
    tracer = provider.get_tracer("switchboard.test.step_events")
    try:
        with run_span(tracer, tenant_id="default", run_id=RUN_ID, orchestrator="fake"):
            with supervisor_span(tracer, run_id=RUN_ID) as plan:
                set_attributes(
                    plan,
                    {
                        "switchboard.plan": "data+docs",
                        "switchboard.plan.reason": "orders and policy",
                    },
                )
            with agent_span(tracer, agent="data", run_id=RUN_ID) as agent:
                with tracer.start_as_current_span(
                    "execute_tool orders_summary@1",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "switchboard.run_id": RUN_ID,
                        "gen_ai.tool.name": "orders_summary@1",
                    },
                ) as tool:
                    set_attributes(
                        tool,
                        {
                            "switchboard.tool.status": "ok",
                            "switchboard.tool.total_rows": 726,
                            "switchboard.tool.audit_id": "audit-1",
                        },
                    )
                set_attributes(agent, {"switchboard.partial": True, "switchboard.citations": 2})
            with tracer.start_as_current_span(
                "gate", attributes={"switchboard.run_id": RUN_ID}
            ) as gate:
                set_attributes(
                    gate,
                    {"switchboard.gate.verdict": "fail", "switchboard.gate.action": "retry"},
                )
    finally:
        provider.shutdown()

    events = drain(queue)
    assert [event.type for event in events] == [
        RUN_STARTED,
        PLAN_DECIDED,
        AGENT_STARTED,
        TOOL_CALLED,
        AGENT_FINISHED,
        GATE_CHECKED,
    ]
    assert events[0].data == {}
    assert events[1].data == {
        "plan": "data+docs",
        "agents": ["data", "docs"],
        "reason": "orders and policy",
    }
    assert events[2].data == {"agent": "data"}
    assert events[3].data["tool"] == "orders_summary@1"
    assert events[3].data["status"] == "ok"
    assert events[3].data["total_rows"] == 726
    assert events[3].data["audit_id"] == "audit-1"
    assert isinstance(events[3].data["duration_ms"], int)
    assert events[4].data == {"agent": "data", "partial": True, "citations": 2}
    assert events[5].data == {"verdict": "fail", "action": "retry"}
    assert all(event.run_id == RUN_ID for event in events)


def test_server_tool_spans_and_unregistered_runs_are_dropped() -> None:
    publisher = StepEventPublisher()
    provider = TracerProvider()
    provider.add_span_processor(StepEventProcessor(publisher))
    tracer = provider.get_tracer("switchboard.test.step_events")
    try:
        # No queue registered for this run: publishing must be a no-op.
        with run_span(tracer, tenant_id="default", run_id=RUN_ID, orchestrator="fake"):
            pass
        assert publisher.active_runs() == []

        queue = publisher.register(RUN_ID)
        with tracer.start_as_current_span(
            "execute_tool orders_summary@1",
            kind=SpanKind.SERVER,
            attributes={"switchboard.run_id": RUN_ID},
        ):
            pass
        # The MCP process publishes its own tool events; the API must not repeat them.
        assert queue.empty()
    finally:
        provider.shutdown()


def test_sse_rendering_is_one_event_block() -> None:
    rendered = StepEvent(PLAN_DECIDED, RUN_ID, {"agents": ["data"]}).to_sse()
    assert rendered.startswith("event: plan.decided\n")
    assert rendered.endswith("\n\n")
    assert '"run_id": "' + RUN_ID + '"' in rendered


def test_answer_delta_goes_only_to_the_run_that_is_streaming() -> None:
    publisher = StepEventPublisher()
    queue = publisher.register(RUN_ID)
    processor = StepEventProcessor(publisher)

    processor.publish_answer_delta(RUN_ID, "There were ")
    processor.publish_answer_delta(RUN_ID, "")
    processor.publish_answer_delta("another-run", "not this queue")

    events = drain(queue)
    assert [event.type for event in events] == [ANSWER_DELTA]
    assert events[0].data == {"text": "There were "}
    assert events[0].run_id == RUN_ID
    assert events[0].to_sse().startswith("event: answer.delta\n")
