"""Map OpenAI SDK exceptions to the core error taxonomy.

Shared by the provider and embedder adapters so both report transient and
request failures the same way (design.md section 15).
"""

from __future__ import annotations

from typing import Any

import openai

from switchboard.core.errors import (
    ProviderRequestError,
    ProviderTransientError,
    SwitchboardError,
)


def map_error(exc: openai.OpenAIError) -> SwitchboardError:
    status = getattr(exc, "status_code", None)
    transient = isinstance(
        exc,
        (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ),
    ) or (isinstance(status, int) and (status >= 500 or status in {408, 429}))
    details: dict[str, Any] = {"status_code": status} if isinstance(status, int) else {}
    body = getattr(exc, "body", None)
    if body:
        details["body"] = body
    message = str(exc)
    if body and str(body) not in message:
        message = f"{message}: {body}"
    if transient:
        return ProviderTransientError(message, details=details)
    return ProviderRequestError(message, details=details)
