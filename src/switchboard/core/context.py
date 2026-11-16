"""The default context policy: clear the oldest consumed tool results.

Design.md section 8.6 and ADR 0012. Token estimation is a deliberately
conservative character heuristic because core has no tokenizer dependency; the
provider's reported usage later shows the estimate's error.
"""

from __future__ import annotations

import math
import re

from switchboard.core.models import ContextBudget, FitResult, Message

CHARS_PER_TOKEN = 3
MESSAGE_OVERHEAD_TOKENS = 4
# CJK scripts do not have space-delimited words and tokenizers typically emit
# about one token per character. Counting a character as a third of a token
# would undercount Chinese history by several times (V3.12).
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(CJK.findall(text))
    other = len(text) - cjk
    return max(1, cjk + math.ceil(other / CHARS_PER_TOKEN))


def estimate_message_tokens(message: Message) -> int:
    return MESSAGE_OVERHEAD_TOKENS + estimate_tokens(message.content)


def cleared_marker(message: Message) -> str:
    result_id = message.result_id or "unknown"
    rows = message.total_rows if message.total_rows is not None else 0
    return f"[result {result_id}: {rows} rows, cleared]"


class ClearOldestToolResults:
    """Replace old tool result bodies with a one-line marker.

    The system prompt and the newest tool result are never touched, and no
    model is called to summarize (ADR 0012).
    """

    name = "clear_oldest_tool_results"

    def fit(self, messages: list[Message], budget: ContextBudget) -> FitResult:
        limit = budget.limit_tokens
        fitted = list(messages)
        estimated = sum(estimate_message_tokens(message) for message in fitted)
        if estimated <= limit:
            return FitResult(
                messages=fitted,
                estimated_tokens=estimated,
                limit_tokens=limit,
                compacted=False,
                cleared_results=0,
            )

        latest_tool = max(
            (index for index, message in enumerate(fitted) if message.role == "tool"),
            default=-1,
        )
        cleared = 0
        for index, message in enumerate(fitted):
            if estimated <= limit:
                break
            if message.role != "tool" or index == latest_tool or message.cleared:
                continue
            before = estimate_message_tokens(message)
            updated = message.model_copy(
                update={"content": cleared_marker(message), "cleared": True}
            )
            fitted[index] = updated
            estimated -= max(0, before - estimate_message_tokens(updated))
            cleared += 1

        return FitResult(
            messages=fitted,
            estimated_tokens=max(0, estimated),
            limit_tokens=limit,
            compacted=cleared > 0,
            cleared_results=cleared,
        )
