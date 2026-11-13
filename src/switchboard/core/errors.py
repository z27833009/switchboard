"""Error taxonomy from design.md section 15.

``retryable`` is a class attribute so a retry policy can ask the class without
inspecting a message. Every error carries optional structured details that the
trace records as span attributes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar


class SwitchboardError(Exception):
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})


class ProviderTransientError(SwitchboardError):
    retryable = True


class ProviderRequestError(SwitchboardError):
    retryable = False


class StructuredOutputError(SwitchboardError):
    retryable = True


class PlanValidationError(SwitchboardError):
    retryable = True

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__(
            "invalid plan: " + "; ".join(reasons),
            details={"reasons": list(reasons)},
        )


class ToolValidationError(SwitchboardError):
    retryable = True


class ToolExecutionError(SwitchboardError):
    retryable = True


class ToolPermissionError(SwitchboardError):
    retryable = False


class RunTokenError(SwitchboardError):
    retryable = False


class RetrievalError(SwitchboardError):
    retryable = True


class CacheError(SwitchboardError):
    """A cache lookup or store failed; the run continues as a cache miss."""

    retryable = True


class BudgetExceeded(SwitchboardError):
    retryable = False


class JudgeError(SwitchboardError):
    """The judge returned output the adapter could not use.

    The caller keeps the layer 1 gate result and records the error, so a
    judge that is unwell never fails a run (design.md section 6.8).
    """

    retryable = False


class CheckFailed(SwitchboardError):
    retryable = True


class RunTimeout(SwitchboardError):
    retryable = False


class ToolApprovalRejected(SwitchboardError):
    """The user rejected a tool call, so the tool was never executed (6.7).

    Agents treat this like a denied call: they record it and answer without the
    result. The LangGraph adapter ends such a run with status ``rejected``.
    """

    retryable = False

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(
            f"the user rejected the tool call {tool}, so nothing was executed",
            details={"tool": tool},
        )


class ApprovalUnavailable(SwitchboardError):
    """The paused graph state is gone, so an approval can no longer resume it.

    Pending approvals live in the in-process checkpointer, so an API restart
    loses them (design.md section 6.7). The API answers 409 and marks the run
    instead of pretending to resume it.
    """

    retryable = False


class ConfigError(SwitchboardError):
    retryable = False


class FixtureMissError(SwitchboardError):
    retryable = False


class RegistryError(SwitchboardError):
    retryable = False


class UnknownComponentError(RegistryError):
    pass
