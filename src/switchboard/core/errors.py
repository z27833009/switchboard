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


class BudgetExceeded(SwitchboardError):
    retryable = False


class CheckFailed(SwitchboardError):
    retryable = True


class RunTimeout(SwitchboardError):
    retryable = False


class ConfigError(SwitchboardError):
    retryable = False


class RegistryError(SwitchboardError):
    retryable = False


class UnknownComponentError(RegistryError):
    pass
