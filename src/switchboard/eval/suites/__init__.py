"""Registered eval suites: routing and toolcall (design.md section 20.1)."""

from switchboard.eval.suites.routing import RoutingSuite, build_routing_request
from switchboard.eval.suites.toolcall import ToolCallSuite, build_toolcall_request

__all__ = [
    "RoutingSuite",
    "ToolCallSuite",
    "build_routing_request",
    "build_toolcall_request",
]
